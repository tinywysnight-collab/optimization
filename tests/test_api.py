import re

import pytest

from hascore import main, render_html
from hascore.input_loader import InputError
from hascore.models import ResourceScore, ServiceScan


class FakeStsClient:
    def get_caller_identity(self):
        return {"Account": "123456789012"}


class FakeSession:
    def client(self, name, region_name=None, config=None):
        return FakeStsClient()


def fake_factory(profile_name):
    return FakeSession()


def fake_scan(session, spec):
    return ServiceScan(
        multi_az=[ResourceScore("rds", "db1", "us-east-1", 20.0, "MultiAZ is enabled")],
        cross_region=[ResourceScore("rds", "db1", "us-east-1", 0.0, "no cross-region read replica")],
    )


@pytest.fixture
def scanners(monkeypatch):
    monkeypatch.setattr("hascore.scan_runner.SCANNERS", {"rds": fake_scan})


@pytest.fixture
def aws_config(tmp_path):
    cfg = tmp_path / "config"
    cfg.write_text("[profile pay-prod]\nsso_account_id = 123456789012\n")
    return cfg


PAYLOAD = {"accounts": [
    {"account_id": "123456789012", "regions": ["us-east-1", "eu-west-1"],
     "pattern_id": "P1", "application": {"name": "pay"}},
    {"account_id": "222222222222", "regions": ["us-east-1"]},
]}


def run(output_format, aws_config):
    return main(PAYLOAD, output_format, aws_config=aws_config, session_factory=fake_factory)


def undated(value):
    """Blank out the run timestamp so two separate runs can be compared."""
    if isinstance(value, dict):
        return {**value, "generated_at": ""}
    return re.sub(r"\d{4}-\d{2}-\d{2}T[\d:.]+\+00:00", "", value)


def test_json_format_returns_the_report_dict(scanners, aws_config):
    report = run("json", aws_config)
    assert isinstance(report, dict)
    acct = next(a for a in report["accounts"] if a["account_id"] == "123456789012")
    assert acct["scores"] == {"multi_az": 20.0, "cross_region": 0.0}
    assert acct["pattern_id"] == "P1"
    assert acct["application"] == {"name": "pay"}


def test_json_is_the_default_format(scanners, aws_config):
    default = main(PAYLOAD, aws_config=aws_config, session_factory=fake_factory)
    assert undated(default) == undated(run("json", aws_config))


def test_account_without_a_profile_is_inaccessible_not_zero(scanners, aws_config):
    report = run("json", aws_config)
    acct = next(a for a in report["accounts"] if a["account_id"] == "222222222222")
    assert acct["accessible"] is False
    assert acct["scores"] == {"multi_az": None, "cross_region": None}


def test_html_format_returns_a_document_string(scanners, aws_config):
    html = run("html", aws_config)
    assert isinstance(html, str)
    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert "123456789012" in html
    assert "MultiAZ is enabled" in html


def test_html_can_be_rendered_from_a_returned_json_report(scanners, aws_config):
    """Callers who want both formats render from the report instead of rescanning."""
    assert undated(render_html(run("json", aws_config))) == undated(run("html", aws_config))


def test_unknown_format_is_rejected(scanners, aws_config):
    with pytest.raises(ValueError, match="output_format"):
        run("pdf", aws_config)


def test_invalid_payload_raises_input_error(aws_config):
    with pytest.raises(InputError, match="accounts"):
        main({}, aws_config=aws_config, session_factory=fake_factory)
