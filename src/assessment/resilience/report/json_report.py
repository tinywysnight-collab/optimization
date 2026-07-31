"""Build the JSON report structure (the source of truth, spec §9)."""
from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from ..models import AccountResult, DimensionResult


def _dimension(dim: DimensionResult) -> dict[str, Any]:
    return {
        "account_score": dim.account_score,
        "service_scores": dim.service_scores,
        "resources": [asdict(r) for r in dim.resources],
        "notes": [asdict(n) for n in dim.notes],
        "failed_services": dim.failed_services,
    }


def _account(result: AccountResult) -> dict[str, Any]:
    spec = result.spec
    return {
        "account_id": spec.account_id,
        "pattern_id": spec.pattern_id,
        "environment": spec.environment,
        "regions": spec.regions,
        "application": spec.application,
        "role_name": spec.role_name,
        "accessible": result.accessible,
        "error": result.error,
        "scores": {
            "multi_az": result.multi_az.account_score,
            "cross_region": result.cross_region.account_score,
        },
        "dimensions": {
            "multi_az": _dimension(result.multi_az),
            "cross_region": _dimension(result.cross_region),
        },
    }


def build_report(results: list[AccountResult]) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": {
            "total_accounts": len(results),
            "inaccessible_accounts": [r.spec.account_id for r in results if not r.accessible],
        },
        "accounts": [_account(r) for r in results],
    }
