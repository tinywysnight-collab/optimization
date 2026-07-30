"""Exception-tag (exemption) semantics: a floor of 50, never a cap.

The keys say *skip*, not *disable*: the tag suppresses a check. This tool never
alters a resource's configuration (spec §0), so a key reading `disable-multiaz`
would name the opposite of what it does and of what the tool is able to do.
"""
from __future__ import annotations

from .models import AwsDict

MULTIAZ_TAG = "skip-multiaz"
CROSSREGION_TAG = "skip-cross-region"
EXEMPT_FLOOR = 50.0


def tags_to_dict(tags: list[AwsDict] | None) -> dict[str, str]:
    """Convert an AWS [{'Key': ..., 'Value': ...}] tag list to a plain dict."""
    if not tags:
        return {}
    return {t["Key"]: t.get("Value", "") for t in tags if "Key" in t}


def apply_exemption(score: float, tags: dict[str, str], tag_key: str) -> tuple[float, bool, str]:
    """Return (final_score, exempted, reason_suffix).

    Key presence alone activates the exemption (case-insensitive); the value
    is ignored. Only scores below the floor are raised.
    """
    present = any(k.lower() == tag_key.lower() for k in tags)
    if present and score < EXEMPT_FLOOR:
        suffix = f"; exception tag '{tag_key}' present, floor raised to 50/100 per exemption rule"
        return EXEMPT_FLOOR, True, suffix
    return score, False, ""
