"""ASG evaluators (spec §5.3, §6): config-based multi-AZ; name-matching cross-region."""
from __future__ import annotations

from typing import Any

from ..models import AccountSpec, AwsDict, ResourceScore, ServiceScan
from ..naming import strip_region
from ..tags import CROSSREGION_TAG, MULTIAZ_TAG, apply_exemption, tags_to_dict
from .aws_fetch import fetch_asg

SERVICE = "asg"
EKS_CLUSTER_TAG = "eks:cluster-name"


def is_eks_asg(group: AwsDict) -> bool:
    """EKS node-group ASGs are excluded from cross-region scoring: their names are
    AWS-generated random strings and EKS is matched at the cluster level (spec §6)."""
    return EKS_CLUSTER_TAG in tags_to_dict(group.get("Tags"))


def evaluate_asg_multiaz(groups: list[AwsDict], region: str) -> list[ResourceScore]:
    results: list[ResourceScore] = []
    for g in groups:
        name = g["AutoScalingGroupName"]
        tags = tags_to_dict(g.get("Tags"))
        azs = sorted(set(g.get("AvailabilityZones", [])))
        origin = f" (EKS node group ASG, cluster '{tags[EKS_CLUSTER_TAG]}')" if EKS_CLUSTER_TAG in tags else ""
        if len(azs) >= 2:
            score = 20.0
            reason = f"configuration covers {len(azs)} AZs: {', '.join(azs)}{origin}"
        else:
            score = 0.0
            reason = f"configuration covers only {len(azs)} AZ: {', '.join(azs) or 'none'}{origin}"
        score, exempted, suffix = apply_exemption(score, tags, MULTIAZ_TAG)
        results.append(ResourceScore(SERVICE, name, region, score, reason + suffix, exempted))
    return results


def evaluate_asg_crossregion(groups: list[AwsDict], standby_names: dict[str, set[str]],
                             primary_region: str) -> list[ResourceScore]:
    """standby_names: {standby_region: set of region-stripped match values}."""
    results: list[ResourceScore] = []
    for g in groups:
        if is_eks_asg(g):
            continue  # scored by the eks dimension at the cluster level
        name = g["AutoScalingGroupName"]
        tags = tags_to_dict(g.get("Tags"))
        mv = strip_region(name)
        hits = sorted(r for r, names in standby_names.items() if mv in names)
        if hits:
            score = 20.0
            reason = (f"name-matching heuristic: after region-stripping ('{mv}'), "
                      f"a matching ASG exists in {', '.join(hits)}")
        else:
            score = 0.0
            reason = (f"name-matching heuristic: no ASG matching '{mv}' found in "
                      f"standby region(s) {', '.join(sorted(standby_names))}")
        score, exempted, suffix = apply_exemption(score, tags, CROSSREGION_TAG)
        results.append(ResourceScore(SERVICE, name, primary_region, score, reason + suffix, exempted))
    return results


def scan(session: Any, spec: AccountSpec) -> ServiceScan:
    primary = spec.regions[0]
    groups = fetch_asg(session, primary)["groups"]
    out = ServiceScan()
    out.multi_az = evaluate_asg_multiaz(groups, primary)
    if spec.standby_regions:
        standby_names = {
            r: {strip_region(g["AutoScalingGroupName"])
                for g in fetch_asg(session, r)["groups"] if not is_eks_asg(g)}
            for r in spec.standby_regions
        }
        out.cross_region = evaluate_asg_crossregion(groups, standby_names, primary)
    return out
