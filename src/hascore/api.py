"""Programmatic entry point: an account payload goes in, a report comes out."""
from __future__ import annotations

from typing import Any

from .assume_role import (
    DEFAULT_ROLE_NAME,
    DEFAULT_SESSION_NAME,
    AssumeRoleSessionFactory,
    build_master_sts_client,
)
from .input_loader import parse_accounts
from .report.html_report import render_html
from .report.json_report import build_report
from .scan_runner import SessionFactory, scan_all

JSON = "json"
HTML = "html"
_FORMATS = (JSON, HTML)


def score(payload: dict[str, Any], output_format: str = JSON, *,
          master_profile: str | None = None,
          role_name: str = DEFAULT_ROLE_NAME,
          session_name: str = DEFAULT_SESSION_NAME,
          external_id: str | None = None,
          workers: int = 8,
          session_factory: SessionFactory | None = None) -> dict[str, Any] | str:
    """Scan the accounts described by `payload` and return the report.

    `payload` is the account list (spec §2): {"accounts": [{account_id, regions,
    pattern_id, application, role_name}, ...]}. `regions[0]` is the primary region.

    Access follows spec §3: credentials come from `master_profile` — a profile in
    the caller's AWS config, or the default credential chain when omitted — and
    that identity assumes `role_name` in each target account. An account entry may
    carry its own `role_name` when it is the odd one out. `external_id` is sent
    only when the trust policy requires it.

    `output_format` selects what comes back: "json" returns the report as a dict
    (serialize it yourself if you need a string), "html" returns a self-contained
    HTML document. Both are rendered from the same scan, so to get both without
    scanning twice, ask for "json" and pass the result to `render_html`.

    An account whose role cannot be assumed is reported as inaccessible with N/A
    scores rather than failing the run.
    """
    if output_format not in _FORMATS:
        raise ValueError(f"output_format must be one of {_FORMATS}, got {output_format!r}")
    specs = parse_accounts(payload)
    factory = session_factory or AssumeRoleSessionFactory(
        build_master_sts_client(master_profile),
        role_name=role_name, session_name=session_name, external_id=external_id)
    report = build_report(scan_all(specs, session_factory=factory, workers=workers))
    return render_html(report) if output_format == HTML else report
