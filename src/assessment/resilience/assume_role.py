"""Cross-account access: assume a role in each target account (spec §3).

One master-account session vends credentials for every account in the payload by
calling `sts:AssumeRole` against a role that exists in each of them. The role
name is configurable because organizations name it differently
(`OrganizationAccountAccessRole`, `SecurityAudit`, a bespoke audit role), and a
single account can override it when it is the odd one out.

`sts:AssumeRole` vends temporary credentials and modifies nothing, so it does not
breach the read-only prime directive in spec §0.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any, cast

from .models import AccountSpec

DEFAULT_ROLE_NAME = "OrganizationAccountAccessRole"
DEFAULT_SESSION_NAME = "hascore-resilience-scan"


def build_role_arn(account_id: str, role_name: str, partition: str = "aws") -> str:
    return f"arn:{partition}:iam::{account_id}:role/{role_name}"


@lru_cache
def partition_for_region(region: str) -> str:
    from botocore.session import Session

    return cast(str, Session().get_partition_for_region(region))


@lru_cache
def known_regions() -> frozenset[str]:
    """Every region botocore can name, across all partitions it ships.

    `get_partition_for_region` only pattern-matches, so it accepts a plausible
    typo like `ap-south-99`; this list is what tells a real region from one that
    merely looks like one.
    """
    from botocore.session import Session

    session = Session()
    regions: set[str] = set()
    for partition in session.get_available_partitions():
        regions |= set(session.get_available_regions("sts", partition_name=partition))
    return frozenset(regions)


class AssumeRoleSessionFactory:
    """Turns an account spec into a boto3 session scoped to that account.

    The STS client is built once from the master session and reused for every
    account: botocore clients are thread-safe for calls, while sessions are not
    safe to build concurrently, so the scan runner's thread pool shares this one.
    """

    def __init__(self, sts_client: Any, role_name: str = DEFAULT_ROLE_NAME,
                 session_name: str = DEFAULT_SESSION_NAME,
                 external_id: str | None = None,
                 session_builder: Any = None) -> None:
        self._sts = sts_client
        self._role_name = role_name
        self._session_name = session_name
        self._external_id = external_id
        self._session_builder = session_builder or _boto3_session

    def __call__(self, spec: AccountSpec) -> Any:
        role_name = spec.role_name or self._role_name
        params: dict[str, Any] = {
            "RoleArn": build_role_arn(
                spec.account_id, role_name, partition_for_region(spec.regions[0])),
            "RoleSessionName": self._session_name,
        }
        if self._external_id:
            params["ExternalId"] = self._external_id
        credentials = self._sts.assume_role(**params)["Credentials"]
        return self._session_builder(
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials["SessionToken"],
        )


def _boto3_session(**credentials: Any) -> Any:
    import boto3
    return boto3.Session(**credentials)


def build_master_sts_client(master_profile: str | None, region: str | None = None) -> Any:
    """STS client for the master account.

    `master_profile` names a profile in the caller's AWS config; None falls back
    to the default credential chain, which is what a role-bearing EC2 or ECS task
    running in the master account already has.
    """
    import boto3
    session = boto3.Session(profile_name=master_profile) if master_profile else boto3.Session()
    return session.client("sts", region_name=region) if region else session.client("sts")
