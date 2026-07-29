from hascore.models import AccountSpec, ResourceScore, ServiceScan
from hascore.scan_runner import SCANNERS, scan_account, scan_all


class FakeStsClient:
    def get_caller_identity(self):
        return {"Account": "123456789012"}


class FakeSession:
    def client(self, name, region_name=None):
        return FakeStsClient()


def fake_factory(spec):
    return FakeSession()


def failing_factory(spec):
    raise RuntimeError("AccessDenied: not authorized to assume")


def rs(service, score):
    return ResourceScore(service=service, resource_id="r1", region="us-east-1", score=score, reason="x")


def spec(regions=("us-east-1", "eu-west-1"), pattern="GS-001"):
    """GS-001 by default: only that pattern puts an account in Cross-Region scope."""
    return AccountSpec("123456789012", list(regions), pattern_id=pattern)


def patch_scanners(monkeypatch, mapping):
    monkeypatch.setattr("hascore.scan_runner.SCANNERS", mapping)


def test_happy_path_aggregates_both_dimensions(monkeypatch):
    patch_scanners(monkeypatch, {
        "rds": lambda session, s: ServiceScan(multi_az=[rs("rds", 20.0)], cross_region=[rs("rds", 0.0)]),
        "asg": lambda session, s: ServiceScan(multi_az=[rs("asg", 0.0)], cross_region=[rs("asg", 20.0)]),
    })
    result = scan_account(spec(), session_factory=fake_factory)
    assert result.accessible
    assert result.multi_az.account_score == 10.0
    assert result.cross_region.account_score == 10.0


def test_inaccessible_account_is_na_not_zero():
    result = scan_account(spec(), session_factory=failing_factory)
    assert not result.accessible
    assert "AccessDenied" in result.error
    assert "assume" in result.error
    assert result.multi_az.account_score is None
    assert result.cross_region.account_score is None


def test_service_failure_is_na_and_other_services_still_scored(monkeypatch):
    def boom(session, s):
        raise RuntimeError("AccessDenied on fsx:DescribeFileSystems")

    patch_scanners(monkeypatch, {
        "rds": lambda session, s: ServiceScan(multi_az=[rs("rds", 20.0)]),
        "fsx": boom,
    })
    result = scan_account(spec(), session_factory=fake_factory)
    assert result.multi_az.account_score == 20.0  # fsx N/A, not 0
    assert "fsx" in result.multi_az.failed_services
    assert any("AccessDenied" in n.message for n in result.multi_az.notes)


def test_account_outside_the_gs001_pattern_has_cross_region_na(monkeypatch):
    """Two regions are not enough — the pattern decides whether a standby is
    expected, so an out-of-scope account is N/A rather than scored 0."""
    patch_scanners(monkeypatch, {
        "rds": lambda session, s: ServiceScan(multi_az=[rs("rds", 20.0)],
                                              cross_region=[rs("rds", 20.0)]),
    })
    result = scan_account(spec(pattern="PATTERN-A1"), session_factory=fake_factory)
    assert result.multi_az.account_score == 20.0
    assert result.cross_region.account_score is None
    assert any("GS-001" in n.message for n in result.cross_region.notes)


def test_scan_all_returns_result_per_spec(monkeypatch):
    patch_scanners(monkeypatch, {
        "rds": lambda session, s: ServiceScan(multi_az=[rs("rds", 20.0)]),
    })
    results = scan_all([spec(), spec()], session_factory=fake_factory, workers=2)
    assert len(results) == 2


def test_scanner_registry_covers_all_nine_services():
    assert set(SCANNERS) == {"rds", "efs", "asg", "opensearch", "fsx", "elasticache", "elb", "eks", "msk"}
