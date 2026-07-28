"""Two-level aggregation: resource -> service dimension -> account (0-20)."""
from __future__ import annotations

from .models import DimensionResult, ResourceScore


def compute_service_scores(resources: list[ResourceScore]) -> dict[str, float | None]:
    by_service: dict[str, list[float]] = {}
    for r in resources:
        by_service.setdefault(r.service, [])
        if r.score is not None:
            by_service[r.service].append(r.score)
    return {
        svc: (round(sum(vals) / len(vals), 1) if vals else None)
        for svc, vals in by_service.items()
    }


def compute_account_score(service_scores: dict[str, float | None]) -> float | None:
    vals = [v for v in service_scores.values() if v is not None]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 1)


def finalize_dimension(dim: DimensionResult) -> None:
    dim.service_scores = compute_service_scores(dim.resources)
    dim.account_score = compute_account_score(dim.service_scores)
