"""ElastiCache evaluators (spec §5.6, §6): Redis/Valkey only; others N/A."""
from __future__ import annotations

from typing import Any

from ..models import AccountSpec, AwsDict, ResourceScore, ServiceScan
from ..tags import CROSSREGION_TAG, MULTIAZ_TAG, apply_exemption
from .aws_fetch import fetch_elasticache

SERVICE = "elasticache"
_SCORED_ENGINES = ("redis", "valkey")


def evaluate_elasticache_multiaz(replication_groups: list[AwsDict], cache_clusters: list[AwsDict],
                                 tags_by_arn: dict[str, dict[str, str]], region: str) -> list[ResourceScore]:
    results: list[ResourceScore] = []
    for group in replication_groups:
        rgid = group["ReplicationGroupId"]
        tags = tags_by_arn.get(group.get("ARN", ""), {})
        state = group.get("MultiAZ", "disabled")
        if state == "enabled":
            score, reason = 20.0, "replication group MultiAZ is enabled"
        else:
            score, reason = 0.0, f"replication group MultiAZ is {state}"
        score, exempted, suffix = apply_exemption(score, tags, MULTIAZ_TAG)
        results.append(ResourceScore(SERVICE, rgid, region, score, reason + suffix, exempted))

    for cluster in cache_clusters:
        if cluster.get("ReplicationGroupId"):
            continue  # member of a replication group, scored there
        ccid = cluster["CacheClusterId"]
        engine = cluster.get("Engine", "")
        tags = tags_by_arn.get(cluster.get("ARN", ""), {})
        if engine.lower() in _SCORED_ENGINES:
            score, exempted, suffix = apply_exemption(
                0.0, tags, MULTIAZ_TAG)
            reason = "standalone single node, no replica" + suffix
            results.append(ResourceScore(SERVICE, ccid, region, score, reason, exempted))
        else:
            reason = (f"engine '{engine}' has no replication mechanism; out of scoring "
                      "scope, recorded N/A")
            results.append(ResourceScore(SERVICE, ccid, region, None, reason))
    return results


def evaluate_elasticache_crossregion(replication_groups: list[AwsDict], cache_clusters: list[AwsDict],
                                     tags_by_arn: dict[str, dict[str, str]],
                                     primary_region: str) -> list[ResourceScore]:
    results: list[ResourceScore] = []
    for group in replication_groups:
        rgid = group["ReplicationGroupId"]
        tags = tags_by_arn.get(group.get("ARN", ""), {})
        global_id = (group.get("GlobalReplicationGroupInfo") or {}).get("GlobalReplicationGroupId")
        if global_id:
            score, reason = 20.0, f"member of Global Datastore '{global_id}'"
        else:
            score, reason = 0.0, "not a member of any Global Datastore"
        score, exempted, suffix = apply_exemption(score, tags, CROSSREGION_TAG)
        results.append(ResourceScore(SERVICE, rgid, primary_region, score, reason + suffix, exempted))

    for cluster in cache_clusters:
        if cluster.get("ReplicationGroupId") or cluster.get("Engine", "").lower() not in _SCORED_ENGINES:
            continue
        ccid = cluster["CacheClusterId"]
        tags = tags_by_arn.get(cluster.get("ARN", ""), {})
        score, exempted, suffix = apply_exemption(0.0, tags, CROSSREGION_TAG)
        reason = "standalone single node, not part of any Global Datastore" + suffix
        results.append(ResourceScore(SERVICE, ccid, primary_region, score, reason, exempted))
    return results


def scan(session: Any, spec: AccountSpec) -> ServiceScan:
    primary = spec.regions[0]
    raw = fetch_elasticache(session, primary)
    out = ServiceScan()
    out.multi_az = evaluate_elasticache_multiaz(
        raw["replication_groups"], raw["cache_clusters"], raw["tags_by_arn"], primary)
    if len(spec.regions) > 1:
        out.cross_region = evaluate_elasticache_crossregion(
            raw["replication_groups"], raw["cache_clusters"], raw["tags_by_arn"], primary)
    return out
