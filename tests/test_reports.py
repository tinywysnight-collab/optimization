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


from hascore.report.html_report import render_html


def test_html_contains_scores_reasons_and_metadata():
    html = render_html(build_report([make_result()]))
    assert "123456789012" in html
    assert "MultiAZ is enabled" in html
    assert "P1" in html
    assert "N/A" in html  # cross-region score for this account
    assert "<html" in html.lower()


def test_html_renders_non_mapping_application_metadata():
    result = AccountResult(spec=AccountSpec(
        "123456789012", ["us-east-1"], application=["payments", "team-x"]))
    html = render_html(build_report([result]))
    assert "payments" in html
    assert "team-x" in html


def test_html_flags_inaccessible_accounts():
    bad = AccountResult(spec=AccountSpec("999999999999", ["us-east-1"]),
                        accessible=False, error="no profile")
    html = render_html(build_report([bad]))
    assert "999999999999" in html
    assert "inaccessible" in html.lower()


def test_html_escapes_untrusted_strings():
    """Reasons, resource ids, and pass-through metadata are externally
    influenced (AWS names, tags, input file); they must never reach the
    HTML unescaped — autoescape must be on despite the .j2 suffix."""
    spec = AccountSpec("123456789012", ["us-east-1"],
                       application={"name": "<img src=x onerror=alert(1)>"})
    result = AccountResult(spec=spec)
    result.multi_az.resources = [ResourceScore(
        "rds", '"><b>bold</b>', "us-east-1", 0.0, "<script>alert(1)</script>")]
    result.multi_az.service_scores = {"rds": 0.0}
    result.multi_az.account_score = 0.0
    html = render_html(build_report([result]))
    assert "<script>alert(1)</script>" not in html
    assert "<img src=x" not in html
    assert "<b>bold</b>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_template_is_declared_as_package_data():
    """The Jinja template must ship inside the wheel. setuptools excludes
    non-Python files unless declared, and a source-tree test cannot tell the
    difference — so guard the packaging declaration itself."""
    import tomllib
    from pathlib import Path

    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    package_data = tomllib.loads(pyproject.read_text())["tool"]["setuptools"]["package-data"]
    assert any(pattern.endswith(".j2") for pattern in package_data["hascore"])


def test_html_supports_locating_an_account():
    """Each account gets an anchor, a jump link, and searchable row metadata so
    a reader can find one account among hundreds without scrolling."""
    html = render_html(build_report([make_result()]))
    assert 'id="acct-123456789012"' in html          # anchor to jump to
    assert 'href="#acct-123456789012"' in html       # link from the summary row
    assert 'data-target="acct-123456789012"' in html  # row click target
    assert 'data-search=' in html                     # filter index
    assert 'id="q"' in html                           # the filter input


def test_html_search_index_covers_the_fields_a_reader_would_type():
    html = render_html(build_report([make_result()]))
    row = next(line for line in html.splitlines() if "data-search=" in line and "123456789012" in line)
    for term in ("123456789012", "P1", "us-east-1", "pay"):
        assert term in row, f"{term!r} missing from the row search index"


def test_html_title_says_resilience():
    html = render_html(build_report([make_result()]))
    assert "Resilience Compliance Report" in html


def test_environment_reaches_both_outputs():
    """Display-only, but it must actually be displayed: JSON carries it and the
    HTML shows it and can be filtered by it."""
    spec = AccountSpec("123456789012", ["us-east-1"], pattern_id="P1",
                       application={"name": "pay"}, environment="production")
    result = AccountResult(spec=spec)
    result.multi_az.service_scores = {"rds": 20.0}
    result.multi_az.account_score = 20.0
    report = build_report([result])

    assert report["accounts"][0]["environment"] == "production"
    html = render_html(report)
    assert "production" in html
    row = next(line for line in html.splitlines()
               if "data-search=" in line and "123456789012" in line)
    assert "production" in row, "environment must be part of the row search index"


def test_missing_environment_renders_without_a_gap():
    spec = AccountSpec("123456789012", ["us-east-1"])
    report = build_report([AccountResult(spec=spec)])
    assert report["accounts"][0]["environment"] is None
    assert "None" not in render_html(report), "a missing environment must not print None"
