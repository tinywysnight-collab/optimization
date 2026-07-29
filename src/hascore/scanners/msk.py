"""MSK evaluators (spec §5.8, §6): broker AZ spread tiered 20/10/0; name-matching cross-region.

Clusters are passed as normalized dicts:
{"name": str, "arn": str, "type": "PROVISIONED"|"SERVERLESS", "subnets": list[str],
 "zone_ids": list[str], "tags": dict}

The control plane (ZooKeeper / KRaft controllers) is AWS-managed and free, like
OpenSearch dedicated masters, so it is not scored. MSK requires every subnet to
sit in a distinct AZ, so the subnet count IS the AZ count when ZoneIds is absent.
"""
from __future__ import annotations

from typing import Any

from ..models import AccountSpec, AwsDict, ResourceScore, ServiceScan
from ..naming import strip_region
from ..tags import CROSSREGION_TAG, MULTIAZ_TAG, apply_exemption
from .aws_fetch import fetch_msk, fetch_msk_cluster_names

SERVICE = "msk"

_SERVERLESS_NOTE = (
    "MSK Serverless is AWS-managed and multi-AZ by design; out of scoring scope "
    "in v1, recorded N/A"
)
_TOPIC_BLIND_SPOT = (
    "; note: topic replication.factor / min.insync.replicas live in the Kafka "
    "data plane and are not covered by this score"
)


def _az_count(cluster: AwsDict) -> int:
    zone_ids = cluster.get("zone_ids") or []
    if zone_ids:
        return len(set(zone_ids))
    return len(cluster.get("subnets") or [])


def evaluate_msk_multiaz(clusters: list[AwsDict], region: str) -> list[ResourceScore]:
    results: list[ResourceScore] = []
    for c in clusters:
        name = c["name"]
        if c.get("type") != "PROVISIONED":
            results.append(ResourceScore(SERVICE, name, region, None, _SERVERLESS_NOTE))
            continue
        azs = _az_count(c)
        if azs >= 3:
            score = 20.0
            reason = ("brokers span 3 AZs (MSK enforces one distinct AZ per subnet); "
                      "ZooKeeper/KRaft controllers are AWS-managed")
        elif azs == 2:
            score = 10.0
            reason = ("brokers span only 2 AZs — replicas of a replication-factor-3 topic "
                      "split 2+1, and losing the majority AZ leaves one in-sync replica, "
                      "so min.insync.replicas=2 blocks producers; MSK recommends three AZs")
        else:
            score = 0.0
            reason = f"brokers span only {azs} AZ(s)"
        reason += _TOPIC_BLIND_SPOT
        score, exempted, suffix = apply_exemption(score, c.get("tags", {}), MULTIAZ_TAG)
        results.append(ResourceScore(SERVICE, name, region, score, reason + suffix, exempted))
    return results


def evaluate_msk_crossregion(clusters: list[AwsDict], standby_names: dict[str, set[str]],
                             primary_region: str) -> list[ResourceScore]:
    """standby_names: {standby_region: set of region-stripped cluster names}."""
    results: list[ResourceScore] = []
    for c in clusters:
        name = c["name"]
        if c.get("type") != "PROVISIONED":
            results.append(ResourceScore(SERVICE, name, primary_region, None, _SERVERLESS_NOTE))
            continue
        mv = strip_region(name)
        hits = sorted(r for r, names in standby_names.items() if mv in names)
        if hits:
            score = 20.0
            reason = (f"name-matching heuristic: after region-stripping ('{mv}'), a matching "
                      f"MSK cluster exists in {', '.join(hits)}")
        else:
            score = 0.0
            reason = (f"name-matching heuristic: no MSK cluster matching '{mv}' found in "
                      f"standby region(s) {', '.join(sorted(standby_names))}")
        score, exempted, suffix = apply_exemption(score, c.get("tags", {}), CROSSREGION_TAG)
        results.append(ResourceScore(SERVICE, name, primary_region, score, reason + suffix, exempted))
    return results


def scan(session: Any, spec: AccountSpec) -> ServiceScan:
    primary = spec.regions[0]
    clusters = fetch_msk(session, primary)["clusters"]
    out = ServiceScan()
    out.multi_az = evaluate_msk_multiaz(clusters, primary)
    if spec.standby_regions:
        standby_names = {
            r: {strip_region(n) for n in fetch_msk_cluster_names(session, r)}
            for r in spec.standby_regions
        }
        out.cross_region = evaluate_msk_crossregion(clusters, standby_names, primary)
    return out
