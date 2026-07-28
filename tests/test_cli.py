import json

from hascore.cli import main
from hascore.models import ResourceScore, ServiceScan


class FakeStsClient:
    def get_caller_identity(self):
        return {"Account": "123456789012"}


class FakeSession:
    def client(self, name, region_name=None):
        return FakeStsClient()


def fake_factory(profile_name):
    return FakeSession()


def write_inputs(tmp_path):
    accounts = tmp_path / "accounts.json"
    accounts.write_text(json.dumps({"accounts": [
        {"account_id": "123456789012", "regions": ["us-east-1", "eu-west-1"],
         "pattern_id": "P1", "application": {"name": "pay"}},
        {"account_id": "222222222222", "regions": ["us-east-1"]},
    ]}))
    aws_config = tmp_path / "aws_config"
    aws_config.write_text("[profile pay-prod]\nsso_account_id = 123456789012\n")
    return accounts, aws_config


def test_end_to_end_produces_json_and_html(tmp_path, monkeypatch):
    monkeypatch.setattr("hascore.scan_runner.SCANNERS", {
        "rds": lambda session, spec: ServiceScan(
            multi_az=[ResourceScore("rds", "db1", "us-east-1", 20.0, "MultiAZ is enabled")],
            cross_region=[ResourceScore("rds", "db1", "us-east-1", 0.0, "no cross-region read replica")],
        ),
    })
    accounts, aws_config = write_inputs(tmp_path)
    out_dir = tmp_path / "out"

    exit_code = main([str(accounts), "-o", str(out_dir), "--aws-config", str(aws_config)],
                     session_factory=fake_factory)
    assert exit_code == 0

    report = json.loads((out_dir / "report.json").read_text())
    acct1 = next(a for a in report["accounts"] if a["account_id"] == "123456789012")
    assert acct1["scores"]["multi_az"] == 20.0
    assert acct1["scores"]["cross_region"] == 0.0
    # account without a matching profile is inaccessible, not zero-scored
    acct2 = next(a for a in report["accounts"] if a["account_id"] == "222222222222")
    assert acct2["accessible"] is False
    assert acct2["scores"]["multi_az"] is None

    html = (out_dir / "report.html").read_text()
    assert "123456789012" in html and "MultiAZ is enabled" in html
