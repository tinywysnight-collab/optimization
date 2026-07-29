"""Parse and validate the caller-supplied account payload (spec §2)."""
from __future__ import annotations

import re
from typing import Any

from .models import AccountSpec

_ACCOUNT_ID = re.compile(r"^\d{12}$")


class InputError(ValueError):
    pass


def parse_accounts(payload: dict[str, Any]) -> list[AccountSpec]:
    if not isinstance(payload, dict) or "accounts" not in payload:
        raise InputError("payload must be a mapping with an 'accounts' array")
    if not isinstance(payload["accounts"], list):
        raise InputError("'accounts' must be an array")
    specs: list[AccountSpec] = []
    for i, raw in enumerate(payload["accounts"]):
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
            role_name=raw.get("role_name"),
        ))
    return specs
