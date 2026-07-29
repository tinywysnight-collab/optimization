import pytest

from hascore.assume_role import (
    DEFAULT_ROLE_NAME,
    AssumeRoleSessionFactory,
    build_role_arn,
)
from hascore.models import AccountSpec


class FakeSts:
    """Records assume_role calls and hands back throwaway credentials."""

    def __init__(self, fail_with=None):
        self.calls = []
        self.fail_with = fail_with

    def assume_role(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail_with:
            raise self.fail_with
        return {"Credentials": {
            "AccessKeyId": "AKIAFAKE", "SecretAccessKey": "secret",
            "SessionToken": "token", "Expiration": "2026-07-28T12:00:00Z"}}


def factory(sts, role_name=DEFAULT_ROLE_NAME, **kw):
    built = []

    def session_builder(**creds):
        built.append(creds)
        return {"session_for": creds["aws_access_key_id"]}

    return AssumeRoleSessionFactory(sts, role_name, session_builder=session_builder, **kw), built


def test_build_role_arn():
    assert build_role_arn("123456789012", "ScanRole") == "arn:aws:iam::123456789012:role/ScanRole"


def test_assumes_the_configured_role_in_the_target_account():
    sts = FakeSts()
    f, built = factory(sts, role_name="ResilienceAudit")
    session = f(AccountSpec("123456789012", ["us-east-1"]))

    assert sts.calls[0]["RoleArn"] == "arn:aws:iam::123456789012:role/ResilienceAudit"
    assert built[0]["aws_access_key_id"] == "AKIAFAKE"
    assert built[0]["aws_session_token"] == "token"
    assert session == {"session_for": "AKIAFAKE"}


def test_role_arn_uses_the_primary_regions_partition():
    sts = FakeSts()
    f, _ = factory(sts, role_name="ResilienceAudit")
    f(AccountSpec("123456789012", ["cn-north-1"]))
    assert sts.calls[0]["RoleArn"] == \
        "arn:aws-cn:iam::123456789012:role/ResilienceAudit"


def test_account_can_override_the_role_name():
    """Some accounts carry a differently-named role; the payload may say so."""
    sts = FakeSts()
    f, _ = factory(sts, role_name="ResilienceAudit")
    f(AccountSpec("999999999999", ["us-east-1"], role_name="LegacyAuditRole"))
    assert sts.calls[0]["RoleArn"] == "arn:aws:iam::999999999999:role/LegacyAuditRole"


def test_session_name_is_sent_and_configurable():
    sts = FakeSts()
    f, _ = factory(sts, session_name="nightly-scan")
    f(AccountSpec("123456789012", ["us-east-1"]))
    assert sts.calls[0]["RoleSessionName"] == "nightly-scan"


def test_external_id_is_only_sent_when_configured():
    sts = FakeSts()
    f, _ = factory(sts)
    f(AccountSpec("123456789012", ["us-east-1"]))
    assert "ExternalId" not in sts.calls[0]

    sts2 = FakeSts()
    f2, _ = factory(sts2, external_id="shared-secret")
    f2(AccountSpec("123456789012", ["us-east-1"]))
    assert sts2.calls[0]["ExternalId"] == "shared-secret"


def test_assume_failure_propagates_so_the_runner_marks_the_account_inaccessible():
    sts = FakeSts(fail_with=RuntimeError("AccessDenied: not authorized to assume"))
    f, _ = factory(sts)
    with pytest.raises(RuntimeError, match="AccessDenied"):
        f(AccountSpec("123456789012", ["us-east-1"]))


def test_one_sts_client_is_shared_across_accounts():
    """The master session is built once; assuming per account must not rebuild
    it, or a few hundred accounts would each pay session construction."""
    sts = FakeSts()
    f, _ = factory(sts)
    for account in ("111111111111", "222222222222", "333333333333"):
        f(AccountSpec(account, ["us-east-1"]))
    assert len(sts.calls) == 3
    assert [c["RoleArn"].split(":")[4] for c in sts.calls] == \
        ["111111111111", "222222222222", "333333333333"]
