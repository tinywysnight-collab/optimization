"""EKS evaluator (spec §6): cross-region dimension only, cluster-level name matching.

Clusters are passed as normalized dicts: {"name": str, "tags": dict}.
Managed node-group ASG names are AWS-generated random strings, so cross-region
matching must happen at the cluster level; this also covers Fargate-only clusters.
"""
from __future__ import annotations

from typing import Any

from ..models import AccountSpec, AwsDict, ResourceScore, ServiceScan
from ..naming import strip_region
from ..tags import CROSSREGION_TAG, apply_exemption
from .aws_fetch import fetch_eks, fetch_eks_cluster_names
from .common import capture_cross_region

SERVICE = "eks"


def evaluate_eks_crossregion(clusters: list[AwsDict], standby_names: dict[str, set[str]],
                             primary_region: str) -> list[ResourceScore]:
    """standby_names: {standby_region: set of region-stripped cluster names}."""
    results: list[ResourceScore] = []
    for c in clusters:
        name = c["name"]
        mv = strip_region(name)
        hits = sorted(r for r, names in standby_names.items() if mv in names)
        if hits:
            score = 20.0
            reason = (f"name-matching heuristic: after region-stripping ('{mv}'), a matching "
                      f"EKS cluster exists in {', '.join(hits)}")
        else:
            score = 0.0
            reason = (f"name-matching heuristic: no EKS cluster matching '{mv}' found in "
                      f"standby region(s) {', '.join(sorted(standby_names))}")
        score, exempted, suffix = apply_exemption(score, c.get("tags", {}), CROSSREGION_TAG)
        results.append(ResourceScore(SERVICE, name, primary_region, score, reason + suffix, exempted))
    return results


def scan(session: Any, spec: AccountSpec) -> ServiceScan:
    primary = spec.regions[0]
    out = ServiceScan()
    if spec.standby_regions:  # EKS is scored in the cross-region dimension only (spec §6)
        def cross_region() -> list[ResourceScore]:
            clusters = fetch_eks(session, primary)["clusters"]
            standby_names = {
                r: {strip_region(n) for n in fetch_eks_cluster_names(session, r)}
                for r in spec.standby_regions
            }
            return evaluate_eks_crossregion(clusters, standby_names, primary)

        capture_cross_region(out, cross_region)
    return out
