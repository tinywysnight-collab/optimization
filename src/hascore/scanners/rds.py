"""RDS evaluators (spec §5.1, §6). Pure functions over describe_* output."""
from __future__ import annotations

from ..models import AwsDict, ResourceScore
from ..tags import CROSSREGION_TAG, MULTIAZ_TAG, apply_exemption, tags_to_dict

SERVICE = "rds"


def _is_aurora(instance: AwsDict) -> bool:
    return (instance.get("Engine") or "").startswith("aurora")


def _is_replica(instance: AwsDict) -> bool:
    return bool(instance.get("ReadReplicaSourceDBInstanceIdentifier"))


def _arn_region(arn: str) -> str:
    return arn.split(":")[3]


def evaluate_rds_multiaz(instances: list[AwsDict], clusters: list[AwsDict], region: str) -> list[ResourceScore]:
    results: list[ResourceScore] = []
    az_by_id = {i["DBInstanceIdentifier"]: i.get("AvailabilityZone") for i in instances}

    for inst in instances:
        if _is_aurora(inst) or _is_replica(inst):
            continue  # Aurora is scored per cluster; replicas are not scored separately
        rid = inst["DBInstanceIdentifier"]
        tags = tags_to_dict(inst.get("TagList"))
        primary_az = inst.get("AvailabilityZone")
        local_replicas = [r for r in inst.get("ReadReplicaDBInstanceIdentifiers", []) if r in az_by_id]
        cross_az = [r for r in local_replicas if az_by_id[r] and az_by_id[r] != primary_az]
        if inst.get("MultiAZ"):
            score, reason = 20.0, "MultiAZ is enabled"
        elif cross_az:
            score = 20.0
            reason = (f"MultiAZ disabled, but read replica '{cross_az[0]}' is in "
                      f"{az_by_id[cross_az[0]]} while the primary is in {primary_az}; "
                      "cross-AZ replica provides AZ redundancy")
        elif local_replicas:
            score = 0.0
            reason = (f"MultiAZ disabled; read replica(s) {local_replicas} share the same AZ "
                      f"{primary_az} as the primary — same-AZ replicas provide no AZ-level redundancy")
        else:
            score, reason = 0.0, "MultiAZ disabled and no read replicas"
        score, exempted, suffix = apply_exemption(score, tags, MULTIAZ_TAG)
        results.append(ResourceScore(SERVICE, rid, region, score, reason + suffix, exempted))

    for cluster in clusters:
        cid = cluster["DBClusterIdentifier"]
        tags = tags_to_dict(cluster.get("TagList"))
        member_azs = {az_by_id.get(m["DBInstanceIdentifier"]) for m in cluster.get("DBClusterMembers", [])}
        member_azs.discard(None)
        if len(member_azs) >= 2:
            score = 20.0
            reason = f"Aurora cluster instances span {len(member_azs)} AZs ({', '.join(sorted(az for az in member_azs if az is not None))})"
        else:
            score = 0.0
            reason = "Aurora cluster has instances in only one AZ — no cross-AZ reader instance"
        score, exempted, suffix = apply_exemption(score, tags, MULTIAZ_TAG)
        results.append(ResourceScore(SERVICE, cid, region, score, reason + suffix, exempted))

    return results


def evaluate_rds_crossregion(instances: list[AwsDict], clusters: list[AwsDict], global_clusters: list[AwsDict],
                             primary_region: str, declared_regions: list[str]) -> list[ResourceScore]:
    results: list[ResourceScore] = []

    for inst in instances:
        if _is_aurora(inst) or _is_replica(inst):
            continue
        rid = inst["DBInstanceIdentifier"]
        tags = tags_to_dict(inst.get("TagList"))
        cross = [r for r in inst.get("ReadReplicaDBInstanceIdentifiers", [])
                 if r.startswith("arn:") and _arn_region(r) != primary_region]
        if cross:
            reg = _arn_region(cross[0])
            reason = f"cross-region read replica exists in {reg}"
            if reg not in declared_regions:
                reason += " (region not in the declared regions list)"
            score = 20.0
        else:
            score, reason = 0.0, "no cross-region read replica"
        score, exempted, suffix = apply_exemption(score, tags, CROSSREGION_TAG)
        results.append(ResourceScore(SERVICE, rid, primary_region, score, reason + suffix, exempted))

    other_regions_by_arn: dict[str, set[str]] = {}
    for gc in global_clusters:
        arns = [m["DBClusterArn"] for m in gc.get("GlobalClusterMembers", [])]
        for arn in arns:
            others = {_arn_region(a) for a in arns} - {_arn_region(arn)}
            other_regions_by_arn[arn] = others

    for cluster in clusters:
        cid = cluster["DBClusterIdentifier"]
        tags = tags_to_dict(cluster.get("TagList"))
        others = other_regions_by_arn.get(cluster.get("DBClusterArn", ""), set())
        if others:
            score = 20.0
            reason = f"member of an Aurora Global Database with cluster(s) in {', '.join(sorted(others))}"
        else:
            score, reason = 0.0, "not part of an Aurora Global Database"
        score, exempted, suffix = apply_exemption(score, tags, CROSSREGION_TAG)
        results.append(ResourceScore(SERVICE, cid, primary_region, score, reason + suffix, exempted))

    return results
