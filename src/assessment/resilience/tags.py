"""Exception-tag (exemption) semantics: a floor of 50, never a cap.

The keys name the *assessment*, not the feature. `disable-multiaz` would read as
an instruction to turn a resource's redundancy off — the opposite of what the tag
does, and of what this tool can do at all (spec §0 forbids any write). Even a
bare `skip-multiaz` can be read as "this resource need not be multi-AZ"; naming
the assessment leaves one reading: do not evaluate this.
"""
from __future__ import annotations

from .models import AwsDict

MULTIAZ_TAG = "skip-multiaz-assessment"
CROSSREGION_TAG = "skip-cross-region-assessment"
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
