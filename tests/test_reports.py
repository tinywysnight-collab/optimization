# tests/test_reports.py
from hascore.models import AccountResult, AccountSpec, ResourceScore, ServiceNote
from hascore.report.json_report import build_report


def make_result():
    spec = AccountSpec("123456789012", ["us-east-1", "eu-west-1"],
                       pattern_id="P1", application={"name": "pay"})
    result = AccountResult(spec=spec)
    result.multi_az.resources = [ResourceScore("rds", "db1", "us-east-1", 20.0, "MultiAZ is enabled")]
    result.multi_az.service_scores = {"rds": 20.0}
    result.multi_az.account_score = 20.0
    result.cross_region.notes = [ServiceNote("fsx", "not scored")]
    result.cross_region.account_score = None
    return result


def test_report_shape_and_passthrough():
    report = build_report([make_result()])
    acct = report["accounts"][0]
    assert acct["account_id"] == "123456789012"
    assert acct["pattern_id"] == "P1"
    assert acct["application"] == {"name": "pay"}
    assert acct["scores"] == {"multi_az": 20.0, "cross_region": None}
    dim = acct["dimensions"]["multi_az"]
    assert dim["service_scores"] == {"rds": 20.0}
    assert dim["resources"][0]["reason"] == "MultiAZ is enabled"
    assert acct["dimensions"]["cross_region"]["notes"][0]["service"] == "fsx"


def test_summary_counts_inaccessible():
    bad = AccountResult(spec=AccountSpec("999999999999", ["us-east-1"]),
                        accessible=False, error="no profile")
    report = build_report([make_result(), bad])
    assert report["summary"]["total_accounts"] == 2
    assert report["summary"]["inaccessible_accounts"] == ["999999999999"]
    assert "generated_at" in report
