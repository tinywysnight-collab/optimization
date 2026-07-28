"""All boto3 I/O lives here. Every function takes a session and a region and
returns plain dicts/lists that the pure evaluators consume."""
from __future__ import annotations

from typing import Any

from botocore.config import Config
from botocore.exceptions import ClientError

from ..models import AwsDict
from ..tags import tags_to_dict

# Spec §10: a few hundred accounts scanned concurrently will hit API throttling;
# adaptive mode backs off and retries instead of surfacing a failed service as N/A.
_CLIENT_CONFIG = Config(retries={"max_attempts": 10, "mode": "adaptive"})


def _paginate(client: Any, op: str, result_key: str, **kwargs: Any) -> list[Any]:
    items: list[Any] = []
    for page in client.get_paginator(op).paginate(**kwargs):
        items.extend(page.get(result_key, []))
    return items


def _collect_next_token(call: Any, result_key: str, **kwargs: Any) -> list[Any]:
    """Manual NextToken paging for operations without a registered paginator."""
    items: list[Any] = []
    token: str | None = None
    while True:
        response = call(**kwargs, NextToken=token) if token else call(**kwargs)
        items.extend(response.get(result_key, []))
        token = response.get("NextToken")
        if not token:
            return items


def fetch_rds(session: Any, region: str) -> dict[str, Any]:
    c = session.client("rds", region_name=region, config=_CLIENT_CONFIG)
    return {
        "instances": _paginate(c, "describe_db_instances", "DBInstances"),
        "clusters": _paginate(c, "describe_db_clusters", "DBClusters"),
        "global_clusters": _paginate(c, "describe_global_clusters", "GlobalClusters"),
    }


def fetch_efs(session: Any, region: str) -> dict[str, Any]:
    c = session.client("efs", region_name=region, config=_CLIENT_CONFIG)
    filesystems = _paginate(c, "describe_file_systems", "FileSystems")
    mount_targets_by_fs = {
        fs["FileSystemId"]: _paginate(c, "describe_mount_targets", "MountTargets",
                                      FileSystemId=fs["FileSystemId"])
        for fs in filesystems
    }
    try:
        replications = _paginate(c, "describe_replication_configurations", "Replications")
    except ClientError as exc:
        # Unlike most describe/list APIs, EFS raises ReplicationNotFound instead
        # of returning an empty list when the region has zero replication configs
        # and no FileSystemId filter was given. That's "no replications", not a
        # scan failure — treat it as one to avoid marking the whole EFS service N/A.
        if exc.response.get("Error", {}).get("Code") != "ReplicationNotFound":
            raise
        replications = []
    return {"filesystems": filesystems, "mount_targets_by_fs": mount_targets_by_fs,
            "replications": replications}


def fetch_asg(session: Any, region: str) -> dict[str, Any]:
    c = session.client("autoscaling", region_name=region, config=_CLIENT_CONFIG)
    return {"groups": _paginate(c, "describe_auto_scaling_groups", "AutoScalingGroups")}


def fetch_opensearch(session: Any, region: str) -> dict[str, Any]:
    c = session.client("opensearch", region_name=region, config=_CLIENT_CONFIG)
    names = [d["DomainName"] for d in c.list_domain_names().get("DomainNames", [])]
    domains: list[AwsDict] = []
    for i in range(0, len(names), 5):  # DescribeDomains accepts at most 5 names
        domains.extend(c.describe_domains(DomainNames=names[i:i + 5]).get("DomainStatusList", []))
    tags_by_arn = {
        d["ARN"]: tags_to_dict(c.list_tags(ARN=d["ARN"]).get("TagList", []))
        for d in domains if d.get("ARN")
    }
    # No boto3 paginator is registered for describe_outbound_connections
    # (get_paginator would raise OperationNotPageableError); page manually.
    connections = _collect_next_token(c.describe_outbound_connections, "Connections")
    return {"domains": domains, "tags_by_arn": tags_by_arn, "connections": connections}


def fetch_opensearch_domain_names(session: Any, region: str) -> list[str]:
    c = session.client("opensearch", region_name=region, config=_CLIENT_CONFIG)
    return [d["DomainName"] for d in c.list_domain_names().get("DomainNames", [])]


def fetch_fsx(session: Any, region: str) -> dict[str, Any]:
    c = session.client("fsx", region_name=region, config=_CLIENT_CONFIG)
    return {"filesystems": _paginate(c, "describe_file_systems", "FileSystems")}


def fetch_fsx_windows_names(session: Any, region: str) -> list[str]:
    """'Name' tag values of Windows file systems, for cross-region name matching."""
    names = []
    for fs in fetch_fsx(session, region)["filesystems"]:
        if fs.get("FileSystemType") == "WINDOWS":
            name = tags_to_dict(fs.get("Tags")).get("Name")
            if name:
                names.append(name)
    return names


def fetch_eks(session: Any, region: str) -> dict[str, Any]:
    """EKS clusters as [{'name', 'tags'}]; tags come from DescribeCluster."""
    c = session.client("eks", region_name=region, config=_CLIENT_CONFIG)
    names = _paginate(c, "list_clusters", "clusters")
    clusters = []
    for name in names:
        described = c.describe_cluster(name=name).get("cluster", {})
        clusters.append({"name": name, "tags": described.get("tags", {}) or {}})
    return {"clusters": clusters}


def fetch_eks_cluster_names(session: Any, region: str) -> list[str]:
    c = session.client("eks", region_name=region, config=_CLIENT_CONFIG)
    return _paginate(c, "list_clusters", "clusters")


def fetch_elb(session: Any, region: str) -> dict[str, Any]:
    """Merge ELBv2 (ALB/NLB) and Classic ELB into [{'name', 'type', 'tags'}]."""
    merged: list[AwsDict] = []

    v2 = session.client("elbv2", region_name=region, config=_CLIENT_CONFIG)
    lbs = _paginate(v2, "describe_load_balancers", "LoadBalancers")
    for i in range(0, len(lbs), 20):  # DescribeTags accepts at most 20 ARNs
        chunk = lbs[i:i + 20]
        tag_descs = v2.describe_tags(
            ResourceArns=[lb["LoadBalancerArn"] for lb in chunk]).get("TagDescriptions", [])
        tags_by_arn = {t["ResourceArn"]: tags_to_dict(t.get("Tags", [])) for t in tag_descs}
        for lb in chunk:
            merged.append({
                "name": lb["LoadBalancerName"],
                "type": lb.get("Type", "unknown"),
                "tags": tags_by_arn.get(lb["LoadBalancerArn"], {}),
                "azs": [z["ZoneName"] for z in lb.get("AvailabilityZones", []) if z.get("ZoneName")],
            })

    classic = session.client("elb", region_name=region, config=_CLIENT_CONFIG)
    clbs = _paginate(classic, "describe_load_balancers", "LoadBalancerDescriptions")
    for i in range(0, len(clbs), 20):  # DescribeTags accepts at most 20 names
        chunk = clbs[i:i + 20]
        tag_descs = classic.describe_tags(
            LoadBalancerNames=[lb["LoadBalancerName"] for lb in chunk]).get("TagDescriptions", [])
        tags_by_name = {t["LoadBalancerName"]: tags_to_dict(t.get("Tags", [])) for t in tag_descs}
        for lb in chunk:
            merged.append({"name": lb["LoadBalancerName"], "type": "classic",
                           "tags": tags_by_name.get(lb["LoadBalancerName"], {}),
                           "azs": lb.get("AvailabilityZones", [])})

    return {"load_balancers": merged}


def fetch_elb_names(session: Any, region: str) -> list[str]:
    v2 = session.client("elbv2", region_name=region, config=_CLIENT_CONFIG)
    names = [lb["LoadBalancerName"] for lb in _paginate(v2, "describe_load_balancers", "LoadBalancers")]
    classic = session.client("elb", region_name=region, config=_CLIENT_CONFIG)
    names += [lb["LoadBalancerName"]
              for lb in _paginate(classic, "describe_load_balancers", "LoadBalancerDescriptions")]
    return names


def fetch_msk(session: Any, region: str) -> dict[str, Any]:
    """MSK clusters as [{'name','arn','type','subnets','zone_ids','tags'}]."""
    c = session.client("kafka", region_name=region, config=_CLIENT_CONFIG)
    clusters: list[AwsDict] = []
    for raw in _paginate(c, "list_clusters_v2", "ClusterInfoList"):
        broker = raw.get("Provisioned", {}).get("BrokerNodeGroupInfo", {})
        clusters.append({
            "name": raw.get("ClusterName", ""),
            "arn": raw.get("ClusterArn", ""),
            "type": raw.get("ClusterType", ""),
            "subnets": broker.get("ClientSubnets", []),
            "zone_ids": broker.get("ZoneIds", []),
            # Kafka tags arrive as a plain {key: value} map, not a Key/Value list.
            "tags": raw.get("Tags") or {},
        })
    return {"clusters": clusters}


def fetch_msk_cluster_names(session: Any, region: str) -> list[str]:
    c = session.client("kafka", region_name=region, config=_CLIENT_CONFIG)
    return [r.get("ClusterName", "") for r in _paginate(c, "list_clusters_v2", "ClusterInfoList")]


def fetch_elasticache(session: Any, region: str) -> dict[str, Any]:
    c = session.client("elasticache", region_name=region, config=_CLIENT_CONFIG)
    groups = _paginate(c, "describe_replication_groups", "ReplicationGroups")
    clusters = _paginate(c, "describe_cache_clusters", "CacheClusters")
    # describe_serverless_caches has a registered botocore paginator (verified via
    # client.can_paginate("describe_serverless_caches") == True), so _paginate applies here too.
    serverless_caches = _paginate(c, "describe_serverless_caches", "ServerlessCaches")
    tags_by_arn: dict[str, dict[str, str]] = {}
    for resource in [*groups, *clusters, *serverless_caches]:
        arn = resource.get("ARN")
        if arn:
            tags_by_arn[arn] = tags_to_dict(
                c.list_tags_for_resource(ResourceName=arn).get("TagList", []))
    return {"replication_groups": groups, "cache_clusters": clusters,
            "serverless_caches": serverless_caches, "tags_by_arn": tags_by_arn}
