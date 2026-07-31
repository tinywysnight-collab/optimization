"""Shared scanner control flow."""
from __future__ import annotations

from collections.abc import Callable

from ..models import AccountSpec, AwsDict, ResourceScore, ServiceScan


def capture_cross_region(
    result: ServiceScan,
    operation: Callable[[], list[ResourceScore]],
) -> None:
    """Run one Cross-Region operation without discarding Multi-AZ results."""
    try:
        result.cross_region = operation()
    except Exception as exc:  # noqa: BLE001 - failure belongs to this dimension only
        result.cross_region_error = str(exc)


def assess_each_region(
    spec: AccountSpec,
    fetch: Callable[[str], AwsDict],
    evaluate: Callable[[AwsDict, str], list[ResourceScore]],
) -> tuple[list[ResourceScore], AwsDict]:
    """Evaluate Multi-AZ in every region this account's pattern covers.

    A PTM account runs independent deployments, so each region is assessed on its
    own; every other account is scanned in the primary alone (spec §5). Returns
    the pooled scores together with the primary region's raw fetch, which the
    Cross-Region pass reuses instead of fetching the same region twice.
    """
    scores: list[ResourceScore] = []
    primary_raw: AwsDict = {}
    for region in spec.multiaz_regions:
        raw = fetch(region)
        if region == spec.regions[0]:
            primary_raw = raw
        scores.extend(evaluate(raw, region))
    return scores, primary_raw
