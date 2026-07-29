"""RDS evaluators (spec §5.1, §6). Pure functions over describe_* output."""
from __future__ import annotations

from typing import Any

from ..models import AccountSpec, AwsDict, ResourceScore, ServiceScan
from ..tags import CROSSREGION_TAG, MULTIAZ_TAG, apply_exemption, tags_to_dict
from .aws_fetch import fetch_rds, fetch_rds_global_clusters
from .common import capture_cross_region

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
        if inst.get("DBClusterIdentifier") or _is_aurora(inst) or _is_replica(inst):
            continue  # DB cluster members and read replicas are scored at their primary unit
        rid = inst["DBInstanceIdentifier"]
        tags = tags_to_dict(inst.get("TagList"))
        primary_az = inst.get("AvailabilityZone")
        all_replicas = inst.get("ReadReplicaDBInstanceIdentifiers", [])
        local_replicas = [r for r in all_replicas if r in az_by_id]
        # Replicas the region scan cannot place are in another region: RDS
        # returns those as ARNs rather than bare identifiers.
        remote_replicas = [r for r in all_replicas if r not in az_by_id]
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
        elif remote_replicas:
            score = 0.0
            reason = (f"MultiAZ disabled and no read replica in {region}; the "
                      f"{len(remote_replicas)} replica(s) outside this region cannot survive an AZ "
                      "failure here (promoting one is a cross-region recovery, scored separately "
                      "in the cross-region dimension)")
        else:
            score, reason = 0.0, "MultiAZ disabled and no read replicas"
        score, exempted, suffix = apply_exemption(score, tags, MULTIAZ_TAG)
        results.append(ResourceScore(SERVICE, rid, region, score, reason + suffix, exempted))

    for cluster in clusters:
        if cluster.get("ReplicationSourceIdentifier"):
            continue
        cid = cluster["DBClusterIdentifier"]
        tags = tags_to_dict(cluster.get("TagList"))
        member_azs = {az_by_id.get(m["DBInstanceIdentifier"]) for m in cluster.get("DBClusterMembers", [])}
        member_azs.discard(None)
        aurora = _is_aurora(cluster)
        cluster_type = "Aurora cluster" if aurora else "RDS Multi-AZ DB cluster"
        configured = aurora or bool(cluster.get("MultiAZ"))
        if configured and len(member_azs) >= 2:
            score = 20.0
            reason = (f"{cluster_type} instances span {len(member_azs)} AZs "
                      f"({', '.join(sorted(az for az in member_azs if az is not None))})")
        else:
            score = 0.0
            if not configured:
                reason = f"{cluster_type} MultiAZ is disabled"
            elif len(member_azs) == 0:
                reason = ("no cluster member instances with a resolvable AZ were found — "
                          "cannot confirm cross-AZ redundancy")
            else:
                reason = f"{cluster_type} has instances in only one AZ — no cross-AZ reader instance"
        score, exempted, suffix = apply_exemption(score, tags, MULTIAZ_TAG)
        results.append(ResourceScore(SERVICE, cid, region, score, reason + suffix, exempted))

    return results


def evaluate_rds_crossregion(instances: list[AwsDict], clusters: list[AwsDict], global_clusters: list[AwsDict],
                             primary_region: str, declared_regions: list[str]) -> list[ResourceScore]:
    results: list[ResourceScore] = []
    if len(declared_regions) < 2:
        raise ValueError(
            "cross-region scoring needs a designated standby region; "
            f"got {declared_regions!r}. scan() only reaches here for in-scope "
            "accounts, which the input loader guarantees have two regions.")
    standby_region = declared_regions[1]

    for inst in instances:
        if _is_aurora(inst) or _is_replica(inst):
            continue
        rid = inst["DBInstanceIdentifier"]
        tags = tags_to_dict(inst.get("TagList"))
        replica_regions = {
            _arn_region(replica)
            for replica in inst.get("ReadReplicaDBInstanceIdentifiers", [])
            if replica.startswith("arn:") and _arn_region(replica) != primary_region
        }
        if standby_region in replica_regions:
            reason = f"cross-region read replica exists in designated standby {standby_region}"
            score = 20.0
        elif replica_regions:
            score = 0.0
            reason = (f"cross-region read replica exists in {', '.join(sorted(replica_regions))}, "
                      f"but none reaches designated standby {standby_region}")
        else:
            score, reason = 0.0, f"no cross-region read replica in designated standby {standby_region}"
        score, exempted, suffix = apply_exemption(score, tags, CROSSREGION_TAG)
        results.append(ResourceScore(SERVICE, rid, primary_region, score, reason + suffix, exempted))

    other_regions_by_arn: dict[str, set[str]] = {}
    for gc in global_clusters:
        arns = [m["DBClusterArn"] for m in gc.get("GlobalClusterMembers", [])]
        for arn in arns:
            others = {_arn_region(a) for a in arns} - {_arn_region(arn)}
            other_regions_by_arn[arn] = others

    for cluster in clusters:
        if cluster.get("ReplicationSourceIdentifier"):
            continue
        cid = cluster["DBClusterIdentifier"]
        tags = tags_to_dict(cluster.get("TagList"))
        if _is_aurora(cluster):
            others = other_regions_by_arn.get(cluster.get("DBClusterArn", ""), set())
            if standby_region in others:
                score = 20.0
                reason = (f"member of an Aurora Global Database with a cluster in "
                          f"designated standby {standby_region}")
            elif others:
                score = 0.0
                reason = (f"Aurora Global Database has cluster(s) in {', '.join(sorted(others))}, "
                          f"but none in designated standby {standby_region}")
            else:
                score, reason = 0.0, (
                    "not part of an Aurora Global Database reaching "
                    f"designated standby {standby_region}")
        else:
            replica_regions = {
                _arn_region(replica)
                for replica in cluster.get("ReadReplicaIdentifiers", [])
                if replica.startswith("arn:") and _arn_region(replica) != primary_region
            }
            if standby_region in replica_regions:
                score = 20.0
                reason = (f"RDS Multi-AZ DB cluster has a cross-region read replica in "
                          f"designated standby {standby_region}")
            elif replica_regions:
                score = 0.0
                reason = (f"RDS Multi-AZ DB cluster has cross-region read replica(s) in "
                          f"{', '.join(sorted(replica_regions))}, but none in designated "
                          f"standby {standby_region}")
            else:
                score, reason = 0.0, (
                    "RDS Multi-AZ DB cluster has no cross-region read "
                    f"replica in designated standby {standby_region}")
        score, exempted, suffix = apply_exemption(score, tags, CROSSREGION_TAG)
        results.append(ResourceScore(SERVICE, cid, primary_region, score, reason + suffix, exempted))

    return results


def scan(session: Any, spec: AccountSpec) -> ServiceScan:
    primary = spec.regions[0]
    raw = fetch_rds(session, primary)
    out = ServiceScan()
    out.multi_az = evaluate_rds_multiaz(raw["instances"], raw["clusters"], primary)
    if spec.standby_regions:
        capture_cross_region(out, lambda: evaluate_rds_crossregion(
            raw["instances"], raw["clusters"], fetch_rds_global_clusters(session, primary),
            primary, spec.regions))
    return out
