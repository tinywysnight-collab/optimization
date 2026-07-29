import pytest
from botocore.exceptions import ClientError

from hascore.scanners.aws_fetch import (
    _collect_next_token,
    _paginate,
    fetch_asg,
    fetch_efs,
    fetch_efs_replications,
    fetch_elasticache_global_replication_groups,
    fetch_msk_cluster_names,
    fetch_opensearch,
    fetch_rds,
)


class FakePaginator:
    def __init__(self, pages):
        self.pages = pages

    def paginate(self, **kwargs):
        return iter(self.pages)


class FakeClient:
    def __init__(self, pages):
        self._pages = pages

    def get_paginator(self, op):
        return FakePaginator(self._pages)


class RecordingSession:
    def __init__(self):
        self.client_kwargs = {}

    def client(self, name, region_name=None, config=None):
        self.client_kwargs = {"name": name, "region_name": region_name, "config": config}
        return FakeClient([])


def test_clients_are_built_with_adaptive_retries():
    session = RecordingSession()
    fetch_asg(session, "us-east-1")
    config = session.client_kwargs["config"]
    assert config is not None
    assert config.retries["mode"] == "adaptive"


def test_fetch_rds_base_does_not_call_the_cross_region_only_global_api():
    class RdsFakeClient:
        def get_paginator(self, op):
            if op == "describe_global_clusters":
                raise AssertionError("base discovery must not call DescribeGlobalClusters")
            key = "DBInstances" if op == "describe_db_instances" else "DBClusters"
            return FakePaginator([{key: []}])

    session_client = RdsFakeClient()
    session = type("S", (), {"client": lambda self, *args, **kwargs: session_client})()

    assert fetch_rds(session, "us-east-1") == {"instances": [], "clusters": []}


def test_fetch_opensearch_base_does_not_call_outbound_connections():
    class OpenSearchFakeClient:
        def list_domain_names(self):
            return {"DomainNames": []}

        def describe_outbound_connections(self, **kwargs):
            raise AssertionError("base discovery must not call cross-region connections")

    session_client = OpenSearchFakeClient()
    session = type("S", (), {"client": lambda self, *args, **kwargs: session_client})()

    assert fetch_opensearch(session, "us-east-1") == {"domains": [], "tags_by_arn": {}}


def test_fetch_msk_standby_names_excludes_serverless_clusters():
    client = FakeClient([{"ClusterInfoList": [
        {"ClusterName": "orders-eu-west-1", "ClusterType": "PROVISIONED"},
        {"ClusterName": "payments-eu-west-1", "ClusterType": "SERVERLESS"},
    ]}])
    session = type("S", (), {"client": lambda self, *args, **kwargs: client})()

    assert fetch_msk_cluster_names(session, "eu-west-1") == ["orders-eu-west-1"]


def test_fetch_elasticache_global_replication_groups_pages_all_groups():
    groups = [{"GlobalReplicationGroupId": "gd-1"}, {"GlobalReplicationGroupId": "gd-2"}]
    client = FakeClient([{"GlobalReplicationGroups": groups}])
    session = type("S", (), {"client": lambda self, *args, **kwargs: client})()

    assert fetch_elasticache_global_replication_groups(session, "us-east-1") == groups


def test_paginate_concatenates_pages_by_key():
    client = FakeClient([{"Items": [1, 2]}, {"Items": [3]}, {"Other": [9]}])
    assert _paginate(client, "any_op", "Items") == [1, 2, 3]


def test_collect_next_token_follows_tokens():
    calls = []

    def fake_call(**kwargs):
        calls.append(kwargs)
        if "NextToken" not in kwargs:
            return {"Connections": [1, 2], "NextToken": "t1"}
        assert kwargs["NextToken"] == "t1"
        return {"Connections": [3]}

    result = _collect_next_token(fake_call, "Connections")

    assert result == [1, 2, 3]
    assert len(calls) == 2
    assert "NextToken" not in calls[0]
    assert calls[1] == {"NextToken": "t1"}


def test_collect_next_token_single_page():
    calls = []

    def fake_call(**kwargs):
        calls.append(kwargs)
        return {"Connections": [1]}

    result = _collect_next_token(fake_call, "Connections")

    assert result == [1]
    assert len(calls) == 1


class _ReplicationNotFoundPaginator:
    def paginate(self, **kwargs):
        raise ClientError(
            {"Error": {"Code": "ReplicationNotFound", "Message": "no replication configured"}},
            "DescribeReplicationConfigurations")


class EfsFakeClient:
    """describe_file_systems/describe_mount_targets succeed; describe_replication_configurations
    raises ReplicationNotFound, exactly as real EFS does when the region has zero
    replication configs and no FileSystemId filter was given."""

    def __init__(self, filesystems):
        self._filesystems = filesystems

    def get_paginator(self, op):
        if op == "describe_replication_configurations":
            return _ReplicationNotFoundPaginator()
        if op == "describe_file_systems":
            return FakePaginator([{"FileSystems": self._filesystems}])
        if op == "describe_mount_targets":
            return FakePaginator([{"MountTargets": []}])
        raise AssertionError(f"unexpected operation: {op}")


def test_fetch_efs_treats_replication_not_found_as_no_replications():
    session_client = EfsFakeClient([{"FileSystemId": "fs-1"}])
    result = fetch_efs_replications(
        type("S", (), {"client": lambda self, *args, **kwargs: session_client})(), "us-east-1")
    assert result == []


def test_fetch_efs_base_does_not_call_the_cross_region_only_replication_api():
    class BaseOnlyEfsClient(EfsFakeClient):
        def get_paginator(self, op):
            if op == "describe_replication_configurations":
                raise AssertionError("base discovery must not call replication APIs")
            return super().get_paginator(op)

    session_client = BaseOnlyEfsClient([{"FileSystemId": "fs-1"}])
    session = type("S", (), {"client": lambda self, *args, **kwargs: session_client})()

    assert fetch_efs(session, "us-east-1") == {
        "filesystems": [{"FileSystemId": "fs-1"}],
        "mount_targets_by_fs": {"fs-1": []},
    }


class _OtherErrorPaginator:
    def paginate(self, **kwargs):
        raise ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "not authorized"}},
            "DescribeReplicationConfigurations")


def test_fetch_efs_still_raises_on_other_client_errors():
    class OtherErrorFakeClient(EfsFakeClient):
        def get_paginator(self, op):
            if op == "describe_replication_configurations":
                return _OtherErrorPaginator()
            return super().get_paginator(op)

    session_client = OtherErrorFakeClient([{"FileSystemId": "fs-1"}])
    with pytest.raises(ClientError, match="AccessDeniedException"):
        fetch_efs_replications(
            type("S", (), {"client": lambda self, *args, **kwargs: session_client})(), "us-east-1")
