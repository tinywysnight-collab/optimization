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
from .aws_fetch import fetch_elb, fetch_elb_typed_names
from .common import capture_cross_region

SERVICE = "elb"
_SCORED_MULTIAZ_TYPE = "network"
# Scope for both dimensions; classic and gateway are out of scope entirely.
_IN_SCOPE_TYPES = ("network", "application")


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
            reason = (f"scoring covers NLB and ALB; this is a '{lb_type}' load balancer, "
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


def evaluate_elb_crossregion(load_balancers: list[AwsDict],
                             standby_index: dict[str, set[tuple[str, str]]],
                             primary_region: str) -> list[ResourceScore]:
    """standby_index: {standby_region: {(type, region-stripped name)}}.

    The type is half the key on purpose. Names collide across types — an ALB and
    an NLB fronting the same service are often named alike — and an NLB is not a
    standby for an ALB: different listeners, different target semantics. Matching
    on name alone would pass an account whose real DR copy does not exist.
    """
    results: list[ResourceScore] = []
    for lb in load_balancers:
        name, lb_type = lb["name"], lb["type"]
        if lb_type not in _IN_SCOPE_TYPES:
            results.append(ResourceScore(SERVICE, name, primary_region, None,
                f"scoring covers NLB and ALB; this is a '{lb_type}' load balancer, recorded N/A"))
            continue
        mv = strip_region(name)
        hits = sorted(r for r, pairs in standby_index.items() if (lb_type, mv) in pairs)
        if hits:
            score = 20.0
            reason = (f"name-matching heuristic: after region-stripping ('{mv}'), a matching "
                      f"{lb_type} load balancer exists in {', '.join(hits)}")
        else:
            score = 0.0
            reason = (f"name-matching heuristic: no {lb_type} load balancer matching '{mv}' "
                      f"found in standby region(s) {', '.join(sorted(standby_index))}")
        score, exempted, suffix = apply_exemption(score, lb.get("tags", {}), CROSSREGION_TAG)
        results.append(ResourceScore(SERVICE, name, primary_region, score, reason + suffix, exempted))
    return results


def scan(session: Any, spec: AccountSpec) -> ServiceScan:
    primary = spec.regions[0]
    raw = fetch_elb(session, primary)
    out = ServiceScan()
    out.multi_az = evaluate_elb_multiaz(raw["load_balancers"], primary)
    if spec.standby_regions:
        def cross_region() -> list[ResourceScore]:
            standby_index = {
                r: {(t, strip_region(n)) for t, n in fetch_elb_typed_names(session, r)}
                for r in spec.standby_regions
            }
            return evaluate_elb_crossregion(raw["load_balancers"], standby_index, primary)

        capture_cross_region(out, cross_region)
    return out
