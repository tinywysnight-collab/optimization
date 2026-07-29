"""Parse and validate the caller-supplied account payload (spec §2)."""
from __future__ import annotations

import re
from typing import Any

from .assume_role import known_regions
from .models import CROSS_REGION_PATTERN, AccountSpec

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
        if not isinstance(raw, dict):
            raise InputError(f"accounts[{i}] must be a mapping")
        account_id = raw.get("account_id", "")
        if not isinstance(account_id, str) or not _ACCOUNT_ID.match(account_id):
            raise InputError(f"accounts[{i}]: account_id must be a 12-digit string, got {account_id!r}")
        regions = raw.get("regions")
        if not isinstance(regions, list) or not regions or not all(isinstance(r, str) and r for r in regions):
            raise InputError(f"accounts[{i}]: regions must be a non-empty list of strings")
        if len(set(regions)) != len(regions):
            raise InputError(f"accounts[{i}]: primary and standby regions must be distinct")
        # Catch a typo here, where it reads as the payload error it is, rather
        # than later as a wall of per-service endpoint failures on that account.
        unknown = [r for r in regions if r not in known_regions()]
        if unknown:
            raise InputError(
                f"accounts[{i}]: unknown region(s) {unknown}. Check the spelling; "
                "if the region is genuinely new, upgrade boto3.")
        pattern_id = raw.get("pattern_id")
        if pattern_id is not None and not isinstance(pattern_id, str):
            raise InputError(f"accounts[{i}]: pattern_id must be a string or null")
        environment = raw.get("environment")
        if environment is not None and not isinstance(environment, str):
            raise InputError(f"accounts[{i}]: environment must be a string or null")
        spec = AccountSpec(
            account_id=account_id,
            regions=regions,
            pattern_id=pattern_id,
            environment=environment,
            application=raw.get("application", {}),
            role_name=raw.get("role_name"),
        )
        # Fail fast: the pattern names exactly one primary and one standby, so a
        # payload that disagrees is a contradiction to surface now, not something
        # to discover mid-scan or to paper over by ignoring the extra regions.
        if spec.cross_region_required and len(regions) != 2:
            missing = "only one region was given" if len(regions) < 2 else \
                f"{len(regions)} were given"
            raise InputError(
                f"accounts[{i}] ({account_id}): pattern '{spec.pattern_id}' carries the "
                f"{CROSS_REGION_PATTERN} marker and so must list exactly two regions "
                f"(primary, then standby), but {missing}: {regions}."
            )
        specs.append(spec)
    return specs
