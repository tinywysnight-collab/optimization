"""Region-stripping normalization for cross-region name matching (spec §6)."""
from __future__ import annotations

import re

# AWS region token (e.g. ap-south-1) with token boundaries so substrings of
# ordinary names (e.g. the 'eb-tier-2' inside 'web-tier-2') never match.
_REGION = re.compile(r"(?<![a-z0-9])[a-z]{2}(?:-[a-z]+)?-[a-z]+-\d(?![0-9])")
_SEP_RUN = re.compile(r"[-_.]{2,}")


def strip_region(name: str) -> str:
    lowered = name.lower()
    out = _REGION.sub("", lowered)
    out = _SEP_RUN.sub(lambda m: m.group(0)[0], out)
    out = out.strip("-_.")
    # A name that IS a region string would strip to ""; fall back to original.
    return out or lowered
