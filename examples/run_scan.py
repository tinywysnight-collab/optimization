"""Example caller for hascore. Copy this, replace the payload, run it.

    uv run python examples/run_scan.py

hascore itself is a library: `score(payload, output_format)` scans the accounts
and returns the result. Everything else — where the payload comes from, what you
do with the report — belongs to you, which is why it lives here and not in the
package.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hascore import render_html, score

MASTER_PROFILE = "master-account"      # profile in ~/.aws/config, or None
ROLE_NAME = "OrganizationAccountAccessRole"   # role assumed in every account


def build_payload() -> dict[str, Any]:
    """Return the accounts to scan.

    Replace this with however your side actually supplies accounts: a database
    query, an internal API call, a file someone hands you, a hardcoded list.
    `regions[0]` is the primary region and is the only one scored for Multi-AZ;
    the rest are standby regions used for cross-region matching. `pattern_id`
    and `application` are carried through to the report untouched. Add
    `"role_name"` to an entry only when that account's audit role is named
    differently from the one passed to score().
    """
    return {
        "accounts": [
            {
                "account_id": "111111111111",
                "regions": ["ap-south-1", "ap-south-2"],
                "pattern_id": "PATTERN-A1",
                "application": {"name": "payments", "owner": "team-x"},
            },
            {
                "account_id": "222222222222",
                "regions": ["ap-south-1"],
                "application": {"name": "reporting"},
            },
        ]
    }


def run() -> None:
    payload = build_payload()

    # Credentials: MASTER_PROFILE assumes ROLE_NAME in each account. Set
    # MASTER_PROFILE to None to use the default chain (an instance/task role in
    # the master account already has it).
    #
    # One scan, then both formats rendered from it. Calling score() twice would
    # scan every account twice — minutes of extra API calls at real scale.
    report = score(payload, "json",
                   master_profile=MASTER_PROFILE, role_name=ROLE_NAME, workers=8)
    html = render_html(report)

    out = Path("out")
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2, default=str))
    (out / "report.html").write_text(html)

    for account in report["accounts"]:
        scores = account["scores"]
        if not account["accessible"]:
            print(f"{account['account_id']}: INACCESSIBLE — {account['error']}")
            continue
        multi_az = scores["multi_az"] if scores["multi_az"] is not None else "N/A"
        cross_region = scores["cross_region"] if scores["cross_region"] is not None else "N/A"
        print(f"{account['account_id']}: multi-az={multi_az} cross-region={cross_region}")

    print(f"\nWrote {out / 'report.json'} and {out / 'report.html'}")


if __name__ == "__main__":
    run()
