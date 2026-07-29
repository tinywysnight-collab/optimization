"""ELB evaluators (spec §5.7, §6): multi-AZ scores NLB only; cross-region covers all types.

Load balancers are passed as normalized dicts:
{"name": str, "type": str, "tags": dict, "azs": list[str]}
(the fetch layer merges ELBv2 and Classic ELB into this shape).
"""
from __future__ import annotations

from typing import Any

from ..models import AccountSpec, AwsDict, ResourceScore, ServiceScan
from ..naming import strip_region
from ..tags import CROSSREGION_TAG, MULTIAZ_TAG, apply_exemption
from .aws_fetch import fetch_elb, fetch_elb_names

SERVICE = "elb"
_SCORED_MULTIAZ_TYPE = "network"


def evaluate_elb_multiaz(load_balancers: list[AwsDict], region: str) -> list[ResourceScore]:
    results: list[ResourceScore] = []
    for lb in load_balancers:
        name, lb_type = lb["name"], lb["type"]
        if lb_type == "application":
            reason = ("AWS enforces at least two AZ subnets when an ALB is created, so there "
                      "is no configuration lever to assess; recorded N/A")
            results.append(ResourceScore(SERVICE, name, region, None, reason))
            continue
        if lb_type != _SCORED_MULTIAZ_TYPE:
            reason = (f"multi-AZ scoring covers NLB only; this is a '{lb_type}' load balancer, "
                      "recorded N/A")
            results.append(ResourceScore(SERVICE, name, region, None, reason))
            continue
        azs = sorted(set(lb.get("azs", [])))
        if len(azs) >= 2:
            score = 20.0
            reason = f"NLB is enabled in {len(azs)} AZs: {', '.join(azs)}"
        else:
            score = 0.0
            reason = f"NLB is enabled in only {len(azs)} AZ: {', '.join(azs) or 'none'}"
        score, exempted, suffix = apply_exemption(score, lb.get("tags", {}), MULTIAZ_TAG)
        results.append(ResourceScore(SERVICE, name, region, score, reason + suffix, exempted))
    return results


def evaluate_elb_crossregion(load_balancers: list[AwsDict], standby_names: dict[str, set[str]],
                             primary_region: str) -> list[ResourceScore]:
    """standby_names: {standby_region: set of region-stripped load balancer names}."""
    results: list[ResourceScore] = []
    for lb in load_balancers:
        name = lb["name"]
        mv = strip_region(name)
        hits = sorted(r for r, names in standby_names.items() if mv in names)
        if hits:
            score = 20.0
            reason = (f"name-matching heuristic: after region-stripping ('{mv}'), a matching "
                      f"{lb['type']} load balancer exists in {', '.join(hits)}")
        else:
            score = 0.0
            reason = (f"name-matching heuristic: no {lb['type']} load balancer matching '{mv}' "
                      f"found in standby region(s) {', '.join(sorted(standby_names))}")
        score, exempted, suffix = apply_exemption(score, lb.get("tags", {}), CROSSREGION_TAG)
        results.append(ResourceScore(SERVICE, name, primary_region, score, reason + suffix, exempted))
    return results


def scan(session: Any, spec: AccountSpec) -> ServiceScan:
    primary = spec.regions[0]
    raw = fetch_elb(session, primary)
    out = ServiceScan()
    out.multi_az = evaluate_elb_multiaz(raw["load_balancers"], primary)
    if spec.standby_regions:
        standby_names = {
            r: {strip_region(n) for n in fetch_elb_names(session, r)}
            for r in spec.standby_regions
        }
        out.cross_region = evaluate_elb_crossregion(raw["load_balancers"], standby_names, primary)
    return out
