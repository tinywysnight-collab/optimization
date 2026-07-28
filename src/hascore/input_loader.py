"""Parse and validate the externally supplied account list (spec §2)."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .models import AccountSpec

_ACCOUNT_ID = re.compile(r"^\d{12}$")


class InputError(ValueError):
    pass


def load_accounts(path: str | Path) -> list[AccountSpec]:
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or "accounts" not in data:
        raise InputError("input file must be a JSON object with an 'accounts' array")
    specs: list[AccountSpec] = []
    for i, raw in enumerate(data["accounts"]):
        account_id = raw.get("account_id", "")
        if not isinstance(account_id, str) or not _ACCOUNT_ID.match(account_id):
            raise InputError(f"accounts[{i}]: account_id must be a 12-digit string, got {account_id!r}")
        regions = raw.get("regions")
        if not isinstance(regions, list) or not regions or not all(isinstance(r, str) and r for r in regions):
            raise InputError(f"accounts[{i}]: regions must be a non-empty list of strings")
        specs.append(AccountSpec(
            account_id=account_id,
            regions=regions,
            pattern_id=raw.get("pattern_id"),
            application=raw.get("application") or {},
            profile=raw.get("profile"),
        ))
    return specs
