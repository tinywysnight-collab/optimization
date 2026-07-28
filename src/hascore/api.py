"""Programmatic entry point: an account payload goes in, a report comes out."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .input_loader import parse_accounts
from .models import AccountSpec
from .profile_resolver import ProfileResolutionError, load_profiles, resolve_profile
from .report.html_report import render_html
from .report.json_report import build_report
from .scan_runner import SessionFactory, scan_all

JSON = "json"
HTML = "html"
_FORMATS = (JSON, HTML)


def score(payload: dict[str, Any], output_format: str = JSON, *,
          aws_config: str | Path | None = None,
          workers: int = 8,
          session_factory: SessionFactory | None = None) -> dict[str, Any] | str:
    """Scan the accounts described by `payload` and return the report.

    `payload` is the account list (spec §2): {"accounts": [{account_id, regions,
    pattern_id, application, profile}, ...]}. `regions[0]` is the primary region.

    `output_format` selects what comes back: "json" returns the report as a dict
    (serialize it yourself if you need a string), "html" returns a self-contained
    HTML document. Both are rendered from the same data, so to get both without
    scanning twice, ask for "json" and pass the result to `render_html`.

    Credentials come from the caller's AWS config; `aws_config` overrides the
    default ~/.aws/config path. An account whose profile cannot be resolved is
    reported as inaccessible with N/A scores rather than failing the run.
    """
    if output_format not in _FORMATS:
        raise ValueError(f"output_format must be one of {_FORMATS}, got {output_format!r}")
    specs = parse_accounts(payload)
    _attach_profiles(specs, aws_config)
    results = scan_all(specs, session_factory=session_factory, workers=workers)
    report = build_report(results)
    return render_html(report) if output_format == HTML else report


def _attach_profiles(specs: list[AccountSpec], aws_config: str | Path | None) -> None:
    """Resolve each account to an AWS profile, recording failures on the spec so
    the runner can mark that account inaccessible instead of aborting the scan."""
    mapping = load_profiles(aws_config)
    for spec in specs:
        try:
            spec.profile = resolve_profile(spec, mapping)
        except ProfileResolutionError as exc:
            spec.profile = None
            spec.profile_error = str(exc)
