"""Exception-tag (exemption) semantics: a floor of 10, never a cap."""
from __future__ import annotations

from .models import AwsDict

MULTIAZ_TAG = "disable-multiaz"
CROSSREGION_TAG = "disable-crossregion"
EXEMPT_FLOOR = 10.0


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
        suffix = f"; exception tag '{tag_key}' present, floor raised to 10/20 per exemption rule"
        return EXEMPT_FLOOR, True, suffix
    return score, False, ""
