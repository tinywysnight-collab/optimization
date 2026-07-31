"""Shared scanner control flow."""
from __future__ import annotations

from collections.abc import Callable

from ..models import ResourceScore, ServiceScan


def capture_cross_region(
    result: ServiceScan,
    operation: Callable[[], list[ResourceScore]],
) -> None:
    """Run one Cross-Region operation without discarding Multi-AZ results."""
    try:
        result.cross_region = operation()
    except Exception as exc:  # noqa: BLE001 - failure belongs to this dimension only
        result.cross_region_error = str(exc)
