"""EFS evaluators (spec §5.2, §6): storage 10 + mount targets 10; replication cross-region."""
from __future__ import annotations

from typing import Any

from ..models import AccountSpec, AwsDict, ResourceScore, ServiceScan
from ..tags import CROSSREGION_TAG, MULTIAZ_TAG, apply_exemption, tags_to_dict
from .aws_fetch import fetch_efs, fetch_efs_replications
from .common import assess_each_region, capture_cross_region

SERVICE = "efs"


def evaluate_efs_multiaz(filesystems: list[AwsDict], mount_targets_by_fs: dict[str, list[AwsDict]],
                         region: str) -> list[ResourceScore]:
    results: list[ResourceScore] = []
    for fs in filesystems:
        fsid = fs["FileSystemId"]
        tags = tags_to_dict(fs.get("Tags"))
        one_zone = bool(fs.get("AvailabilityZoneId"))
        storage_pts = 0 if one_zone else 50
        mts = mount_targets_by_fs.get(fsid, [])
        mt_azs = {mt["AvailabilityZoneId"] for mt in mts if mt.get("AvailabilityZoneId")}
        if not mt_azs:
            mt_azs = {mt.get("AvailabilityZoneName") for mt in mts} - {None}
        mt_pts = 50 if len(mt_azs) >= 2 else 0
        storage_word = "One Zone" if one_zone else "Regional"
        reason = (f"{storage_word} storage class ({storage_pts}/50); "
                  f"mount targets in {len(mt_azs)} AZ(s) ({mt_pts}/50)")
        score, exempted, suffix = apply_exemption(float(storage_pts + mt_pts), tags, MULTIAZ_TAG)
        results.append(ResourceScore(SERVICE, fsid, region, score, reason + suffix, exempted))
    return results


def evaluate_efs_crossregion(filesystems: list[AwsDict], replications: list[AwsDict],
                             primary_region: str, declared_regions: list[str]) -> list[ResourceScore]:
    if len(declared_regions) < 2:
        raise ValueError(
            "cross-region scoring needs a designated standby region; "
            f"got {declared_regions!r}. scan() only reaches here for in-scope "
            "accounts, which the input loader guarantees have two regions.")
    standby_region = declared_regions[1]
    dest_by_fs: dict[str, set[str]] = {}
    for rep in replications:
        for dest in rep.get("Destinations", []):
            reg = dest.get("Region")
            if reg and reg != primary_region:
                dest_by_fs.setdefault(rep.get("SourceFileSystemId", ""), set()).add(reg)

    results: list[ResourceScore] = []
    for fs in filesystems:
        fsid = fs["FileSystemId"]
        tags = tags_to_dict(fs.get("Tags"))
        dests = dest_by_fs.get(fsid, set())
        if standby_region in dests:
            reason = f"EFS replication configured to designated standby {standby_region}"
            score = 100.0
        elif dests:
            score = 0.0
            reason = (f"EFS replication configured to {', '.join(sorted(dests))}, but not "
                      f"to designated standby {standby_region}")
        else:
            score, reason = 0.0, (
                "no cross-region EFS replication configuration to "
                f"designated standby {standby_region}")
        score, exempted, suffix = apply_exemption(score, tags, CROSSREGION_TAG)
        results.append(ResourceScore(SERVICE, fsid, primary_region, score, reason + suffix, exempted))
    return results


def scan(session: Any, spec: AccountSpec) -> ServiceScan:
    primary = spec.regions[0]
    out = ServiceScan()
    out.multi_az, raw = assess_each_region(
        spec,
        lambda r: fetch_efs(session, r),
        lambda raw, r: evaluate_efs_multiaz(raw["filesystems"], raw["mount_targets_by_fs"], r))
    if spec.standby_regions:
        capture_cross_region(out, lambda: evaluate_efs_crossregion(
            raw["filesystems"], fetch_efs_replications(session, primary), primary, spec.regions))
    return out
