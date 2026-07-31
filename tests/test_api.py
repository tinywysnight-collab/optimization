import re

import pytest

from assessment.resilience import render_html, score
from assessment.resilience.input_loader import InputError
from assessment.resilience.models import ResourceScore, ServiceScan


class FakeStsClient:
    def get_caller_identity(self):
        return {"Account": "123456789012"}


class FakeSession:
    def client(self, name, region_name=None, config=None):
        return FakeStsClient()


def fake_factory(spec):
    return FakeSession()


def fake_scan(session, spec):
    return ServiceScan(
        multi_az=[ResourceScore("rds", "db1", "us-east-1", 100.0, "MultiAZ is enabled")],
        cross_region=[ResourceScore("rds", "db1", "us-east-1", 0.0, "no cross-region read replica")],
    )


@pytest.fixture
def scanners(monkeypatch):
    monkeypatch.setattr("assessment.resilience.scan_runner.SCANNERS", {"rds": fake_scan})


PAYLOAD = {"accounts": [
    {"account_id": "123456789012", "regions": ["us-east-1", "eu-west-1"],
     "pattern_id": "GS-001", "application": {"name": "pay"}},
    {"account_id": "222222222222", "regions": ["us-east-1"]},
]}


def run(output_format):
    return score(PAYLOAD, output_format, session_factory=fake_factory)


def undated(value):
    """Blank out the run timestamp so two separate runs can be compared."""
    if isinstance(value, dict):
        return {**value, "generated_at": ""}
    return re.sub(r"\d{4}-\d{2}-\d{2}T[\d:.]+\+00:00", "", value)


def test_json_format_returns_the_report_dict(scanners):
    report = run("json")
    assert isinstance(report, dict)
    acct = next(a for a in report["accounts"] if a["account_id"] == "123456789012")
    assert acct["scores"] == {"multi_az": 100.0, "cross_region": 0.0}
    assert acct["pattern_id"] == "GS-001"
    assert acct["application"] == {"name": "pay"}


def test_json_is_the_default_format(scanners):
    default = score(PAYLOAD, session_factory=fake_factory)
    assert undated(default) == undated(run("json"))


def test_account_whose_role_cannot_be_assumed_is_inaccessible_not_zero(scanners):
    """One unreachable account must not fail the run or score as zero."""
    def selective(spec):
        if spec.account_id == "222222222222":
            raise RuntimeError("AccessDenied: not authorized to assume")
        return FakeSession()

    report = score(PAYLOAD, session_factory=selective)
    bad = next(a for a in report["accounts"] if a["account_id"] == "222222222222")
    assert bad["accessible"] is False
    assert bad["scores"] == {"multi_az": None, "cross_region": None}
    assert "assume" in bad["error"]
    good = next(a for a in report["accounts"] if a["account_id"] == "123456789012")
    assert good["accessible"] is True


def test_role_name_reaches_the_factory(scanners):
    """The configured role, and any per-account override, must arrive intact."""
    seen = []

    def recording(spec):
        seen.append((spec.account_id, spec.role_name))
        return FakeSession()

    payload = {"accounts": [
        {"account_id": "123456789012", "regions": ["us-east-1"]},
        {"account_id": "222222222222", "regions": ["us-east-1"], "role_name": "LegacyAuditRole"},
    ]}
    score(payload, session_factory=recording)
    assert seen == [("123456789012", None), ("222222222222", "LegacyAuditRole")]


def test_html_format_returns_a_document_string(scanners):
    html = run("html")
    assert isinstance(html, str)
    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert "123456789012" in html
    assert "MultiAZ is enabled" in html


def test_html_can_be_rendered_from_a_returned_json_report(scanners):
    """Callers who want both formats render from the report instead of rescanning."""
    assert undated(render_html(run("json"))) == undated(run("html"))


def test_unknown_format_is_rejected(scanners):
    with pytest.raises(ValueError, match="output_format"):
        run("pdf")


def test_invalid_payload_raises_input_error():
    with pytest.raises(InputError, match="accounts"):
        score({}, session_factory=fake_factory)
