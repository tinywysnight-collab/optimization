"""Two-level aggregation: resource -> service dimension -> account (0-100)."""
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


def compute_service_scores_by_region(
    resources: list[ResourceScore],
) -> dict[str, dict[str, float | None]]:
    """Same two-level mean, split by region: {service: {region: score}}.

    Display only (spec §4). A pooled service score hides which region dragged it
    down; this locates it. It never feeds back into the account score.
    """
    by_service: dict[str, dict[str, list[float]]] = {}
    for r in resources:
        regions = by_service.setdefault(r.service, {})
        regions.setdefault(r.region, [])
        if r.score is not None:
            regions[r.region].append(r.score)
    return {
        service: {
            region: (round(sum(vals) / len(vals), 1) if vals else None)
            for region, vals in regions.items()
        }
        for service, regions in by_service.items()
    }


def compute_account_score(service_scores: dict[str, float | None]) -> float | None:
    vals = [v for v in service_scores.values() if v is not None]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 1)


def finalize_dimension(dim: DimensionResult) -> None:
    dim.service_scores = compute_service_scores(dim.resources)
    dim.account_score = compute_account_score(dim.service_scores)
    # Only worth showing when the dimension spans regions; with one region the
    # breakdown would just restate service_scores.
    if len({r.region for r in dim.resources}) > 1:
        dim.service_scores_by_region = compute_service_scores_by_region(dim.resources)
