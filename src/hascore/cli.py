"""CLI entry point: hascore <accounts.json> [-o out] [--workers N] [--aws-config PATH]."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .input_loader import load_accounts
from .profile_resolver import ProfileResolutionError, load_profiles, resolve_profile
from .report.html_report import render_html
from .report.json_report import build_report
from .scan_runner import SessionFactory, scan_all


def main(argv: list[str] | None = None,
         session_factory: SessionFactory | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hascore", description="AWS HA compliance scorer")
    parser.add_argument("input", help="path to the accounts JSON file")
    parser.add_argument("-o", "--output-dir", default="out", help="report output directory")
    parser.add_argument("--workers", type=int, default=8, help="concurrent account scans")
    parser.add_argument("--aws-config", default=None, help="override ~/.aws/config path")
    args = parser.parse_args(argv)

    specs = load_accounts(args.input)
    mapping = load_profiles(args.aws_config)
    for spec in specs:
        try:
            spec.profile = resolve_profile(spec, mapping)
        except ProfileResolutionError as exc:
            spec.profile = None
            spec.profile_error = str(exc)

    results = scan_all(specs, session_factory=session_factory, workers=args.workers)

    report = build_report(results)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, default=str))
    (out_dir / "report.html").write_text(render_html(report))

    inaccessible = report["summary"]["inaccessible_accounts"]
    print(f"Scanned {len(results)} account(s); {len(inaccessible)} inaccessible.")
    for r in results:
        maz = r.multi_az.account_score
        cr = r.cross_region.account_score
        status = "INACCESSIBLE" if not r.accessible else (
            f"multi-az={maz if maz is not None else 'N/A'} "
            f"cross-region={cr if cr is not None else 'N/A'}")
        print(f"  {r.spec.account_id}: {status}")
    print(f"Reports written to {out_dir / 'report.json'} and {out_dir / 'report.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
