"""ElastiCache evaluators (spec §5.6, §6): Redis/Valkey only; others N/A."""
from __future__ import annotations

from typing import Any

from ..models import AccountSpec, AwsDict, ResourceScore, ServiceScan
from ..tags import CROSSREGION_TAG, MULTIAZ_TAG, apply_exemption
from .aws_fetch import fetch_elasticache, fetch_elasticache_global_replication_groups
from .common import assess_each_region, capture_cross_region

SERVICE = "elasticache"
_SCORED_ENGINES = ("redis", "valkey")


def evaluate_elasticache_multiaz(replication_groups: list[AwsDict], cache_clusters: list[AwsDict],
                                 serverless_caches: list[AwsDict],
                                 tags_by_arn: dict[str, dict[str, str]], region: str) -> list[ResourceScore]:
    results: list[ResourceScore] = []
    for group in replication_groups:
        rgid = group["ReplicationGroupId"]
        tags = tags_by_arn.get(group.get("ARN", ""), {})
        state = group.get("MultiAZ", "disabled")
        if state == "enabled":
            score, reason = 100.0, "replication group MultiAZ is enabled"
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

    for cache in serverless_caches:
        scid = cache["ServerlessCacheName"]
        engine = cache.get("Engine", "")
        reason = (f"ElastiCache Serverless cache (engine '{engine}') is managed cross-AZ by AWS "
                  "and has no user-configurable HA setting; out of scoring scope, recorded N/A")
        results.append(ResourceScore(SERVICE, scid, region, None, reason))
    return results


def evaluate_elasticache_crossregion(replication_groups: list[AwsDict], cache_clusters: list[AwsDict],
                                     serverless_caches: list[AwsDict],
                                     tags_by_arn: dict[str, dict[str, str]],
                                     global_replication_groups: list[AwsDict],
                                     primary_region: str, standby_region: str) -> list[ResourceScore]:
    member_regions_by_global_id = {
        group["GlobalReplicationGroupId"]: {
            member["ReplicationGroupRegion"]
            for member in group.get("Members", [])
            if member.get("ReplicationGroupRegion")
        }
        for group in global_replication_groups
        if group.get("GlobalReplicationGroupId")
    }
    results: list[ResourceScore] = []
    for group in replication_groups:
        rgid = group["ReplicationGroupId"]
        tags = tags_by_arn.get(group.get("ARN", ""), {})
        global_id = (group.get("GlobalReplicationGroupInfo") or {}).get("GlobalReplicationGroupId")
        member_regions = member_regions_by_global_id.get(global_id, set())
        if global_id and standby_region in member_regions:
            score = 100.0
            reason = (f"member of Global Datastore '{global_id}' with a member in "
                      f"designated standby {standby_region}")
        elif global_id and member_regions:
            score = 0.0
            reason = (f"Global Datastore '{global_id}' has members in "
                      f"{', '.join(sorted(member_regions))}, but none in designated "
                      f"standby {standby_region}")
        elif global_id:
            score = 0.0
            reason = (f"member of Global Datastore '{global_id}', but its member Regions "
                      f"could not confirm designated standby {standby_region}")
        else:
            score, reason = 0.0, "not a member of any Global Datastore"
        score, exempted, suffix = apply_exemption(score, tags, CROSSREGION_TAG)
        results.append(ResourceScore(SERVICE, rgid, primary_region, score, reason + suffix, exempted))

    for cluster in cache_clusters:
        if cluster.get("ReplicationGroupId"):
            continue
        ccid = cluster["CacheClusterId"]
        engine = cluster.get("Engine", "")
        if engine.lower() not in _SCORED_ENGINES:
            reason = (f"engine '{engine}' has no Global Datastore mechanism; out of "
                      "Cross-Region scoring scope, recorded N/A")
            results.append(ResourceScore(SERVICE, ccid, primary_region, None, reason))
            continue
        tags = tags_by_arn.get(cluster.get("ARN", ""), {})
        score, exempted, suffix = apply_exemption(0.0, tags, CROSSREGION_TAG)
        reason = "standalone single node, not part of any Global Datastore" + suffix
        results.append(ResourceScore(SERVICE, ccid, primary_region, score, reason, exempted))

    for cache in serverless_caches:
        scid = cache["ServerlessCacheName"]
        engine = cache.get("Engine", "")
        reason = (f"ElastiCache Serverless cache (engine '{engine}') has no Global Datastore "
                  "equivalent; out of scoring scope, recorded N/A")
        results.append(ResourceScore(SERVICE, scid, primary_region, None, reason))
    return results


def scan(session: Any, spec: AccountSpec) -> ServiceScan:
    primary = spec.regions[0]
    out = ServiceScan()
    out.multi_az, raw = assess_each_region(
        spec,
        lambda r: fetch_elasticache(session, r),
        lambda raw, r: evaluate_elasticache_multiaz(raw["replication_groups"], raw["cache_clusters"], raw["serverless_caches"], raw["tags_by_arn"], r))
    if spec.standby_regions:
        capture_cross_region(out, lambda: evaluate_elasticache_crossregion(
            raw["replication_groups"], raw["cache_clusters"], raw["serverless_caches"],
            raw["tags_by_arn"],
            fetch_elasticache_global_replication_groups(session, primary),
            primary, spec.standby_regions[0]))
    return out
