"""OpenSearch evaluators (spec §5.4, §6): data plane 10 + control plane 10; name-matching cross-region."""
from __future__ import annotations

from typing import Any

from ..models import AccountSpec, AwsDict, ResourceScore, ServiceScan
from ..naming import strip_region
from ..tags import CROSSREGION_TAG, MULTIAZ_TAG, apply_exemption
from .aws_fetch import (
    fetch_opensearch,
    fetch_opensearch_connections,
    fetch_opensearch_domain_names,
)
from .common import capture_cross_region

SERVICE = "opensearch"


def evaluate_opensearch_multiaz(domains: list[AwsDict], tags_by_arn: dict[str, dict[str, str]],
                                region: str) -> list[ResourceScore]:
    results: list[ResourceScore] = []
    for d in domains:
        name = d["DomainName"]
        tags = tags_by_arn.get(d.get("ARN", ""), {})
        cfg = d.get("ClusterConfig", {})
        za = bool(cfg.get("ZoneAwarenessEnabled"))
        # Zone awareness without an explicit config is the old-style two-AZ form.
        az_count = cfg.get("ZoneAwarenessConfig", {}).get("AvailabilityZoneCount", 2) if za else 1
        dedicated = bool(cfg.get("DedicatedMasterEnabled"))

        if dedicated:
            # Master placement is AWS-managed (spread over three AZs on its own),
            # so only the data-node spread is the operator's decision: binary.
            if az_count >= 2:
                score = 20.0
                reason = (f"data nodes span {az_count} AZs (zone awareness enabled); dedicated "
                          "masters present, and their placement is AWS-managed")
            else:
                score = 0.0
                reason = ("zone awareness disabled — data nodes sit in a single AZ; healthy "
                          "dedicated masters cannot protect data that has no cross-AZ replica")
            masters = cfg.get("DedicatedMasterCount", 0)
            if masters < 3 or masters % 2 == 0:
                # Advisory only: 7.x+ drops one voter to keep the set odd (2 acts as 1).
                reason += (f"; note: {masters} dedicated master(s) — OpenSearch keeps an odd "
                           "voting set, so even counts round down (2 acts as 1); use 3 or 5")
        else:
            # Data nodes hold the master role, so their AZ spread decides quorum.
            if az_count >= 3:
                score = 20.0
                reason = ("data nodes span 3 AZs; no dedicated masters — data nodes hold the "
                          "master role, and three AZs keep a majority through any single-AZ loss")
            elif az_count == 2:
                score = 10.0
                reason = ("data nodes span only 2 AZs with no dedicated masters — a partition "
                          "between the two AZs risks split-brain, and losing the larger AZ "
                          "loses master quorum")
            else:
                score = 0.0
                reason = "zone awareness disabled — data nodes sit in a single AZ"
        score, exempted, suffix = apply_exemption(score, tags, MULTIAZ_TAG)
        results.append(ResourceScore(SERVICE, name, region, score, reason + suffix, exempted))
    return results


def evaluate_opensearch_crossregion(domains: list[AwsDict], tags_by_arn: dict[str, dict[str, str]],
                                    standby_domains: dict[str, set[str]], connections: list[AwsDict],
                                    primary_region: str) -> list[ResourceScore]:
    """standby_domains: {standby_region: set of region-stripped domain names}."""
    evidence: dict[str, set[str]] = {}
    for c in connections:
        remote = c.get("RemoteDomainInfo", {}).get("AWSDomainInformation", {})
        local = c.get("LocalDomainInfo", {}).get("AWSDomainInformation", {})
        status = c.get("ConnectionStatus", {}).get("StatusCode")
        if status == "ACTIVE" and remote.get("Region") and remote["Region"] != primary_region:
            evidence.setdefault(local.get("DomainName", ""), set()).add(remote["Region"])

    results: list[ResourceScore] = []
    for d in domains:
        name = d["DomainName"]
        tags = tags_by_arn.get(d.get("ARN", ""), {})
        mv = strip_region(name)
        hits = sorted(r for r, names in standby_domains.items() if mv in names)
        if hits:
            score = 20.0
            reason = (f"name-matching heuristic: after region-stripping ('{mv}'), "
                      f"a matching domain exists in {', '.join(hits)}")
        else:
            score = 0.0
            reason = (f"name-matching heuristic: no domain matching '{mv}' found in "
                      f"standby region(s) {', '.join(sorted(standby_domains))}")
        if name in evidence:
            reason += (f"; supporting evidence: ACTIVE cross-region connection(s) to "
                       f"{', '.join(sorted(evidence[name]))} (may be cross-cluster search or replication)")
        score, exempted, suffix = apply_exemption(score, tags, CROSSREGION_TAG)
        results.append(ResourceScore(SERVICE, name, primary_region, score, reason + suffix, exempted))
    return results


def scan(session: Any, spec: AccountSpec) -> ServiceScan:
    primary = spec.regions[0]
    raw = fetch_opensearch(session, primary)
    out = ServiceScan()
    out.multi_az = evaluate_opensearch_multiaz(raw["domains"], raw["tags_by_arn"], primary)
    if spec.standby_regions:
        def cross_region() -> list[ResourceScore]:
            standby_domains = {
                r: {strip_region(n) for n in fetch_opensearch_domain_names(session, r)}
                for r in spec.standby_regions
            }
            return evaluate_opensearch_crossregion(
                raw["domains"], raw["tags_by_arn"], standby_domains,
                fetch_opensearch_connections(session, primary), primary)

        capture_cross_region(out, cross_region)
    return out
