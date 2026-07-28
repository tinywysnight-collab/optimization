"""OpenSearch evaluators (spec §5.4, §6): data plane 10 + control plane 10; name-matching cross-region."""
from __future__ import annotations

from typing import Any

from ..models import AccountSpec, AwsDict, ResourceScore, ServiceScan
from ..naming import strip_region
from ..tags import CROSSREGION_TAG, MULTIAZ_TAG, apply_exemption
from .aws_fetch import fetch_opensearch, fetch_opensearch_domain_names

SERVICE = "opensearch"


def evaluate_opensearch_multiaz(domains: list[AwsDict], tags_by_arn: dict[str, dict[str, str]],
                                region: str) -> list[ResourceScore]:
    results: list[ResourceScore] = []
    for d in domains:
        name = d["DomainName"]
        tags = tags_by_arn.get(d.get("ARN", ""), {})
        cfg = d.get("ClusterConfig", {})
        za = bool(cfg.get("ZoneAwarenessEnabled"))
        az_count = cfg.get("ZoneAwarenessConfig", {}).get("AvailabilityZoneCount", 1) if za else 1
        if cfg.get("DedicatedMasterEnabled"):
            masters = cfg.get("DedicatedMasterCount", 0)
            master_src = "dedicated masters"
        else:
            masters = cfg.get("InstanceCount", 0)
            master_src = "data nodes (no dedicated masters)"
        data_pts = 10 if za else 0
        control_ok = masters >= 3 and masters % 2 == 1 and az_count == 3
        control_pts = 10 if control_ok else 0
        reason = (f"zone awareness {'enabled' if za else 'disabled'} ({data_pts}/10); "
                  f"{masters} master-eligible {master_src} across {az_count} AZ(s)")
        if not control_ok and za:
            reason += " — a single-AZ failure may lose master quorum"
        reason += f" ({control_pts}/10)"
        score, exempted, suffix = apply_exemption(float(data_pts + control_pts), tags, MULTIAZ_TAG)
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
    if len(spec.regions) > 1:
        standby_domains = {
            r: {strip_region(n) for n in fetch_opensearch_domain_names(session, r)}
            for r in spec.regions[1:]
        }
        out.cross_region = evaluate_opensearch_crossregion(
            raw["domains"], raw["tags_by_arn"], standby_domains, raw["connections"], primary)
    return out
