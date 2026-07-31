"""FSx evaluators (spec §5.5, §6): Windows type only; other types recorded N/A."""
from __future__ import annotations

from typing import Any

from ..models import AccountSpec, AwsDict, ResourceScore, ServiceNote, ServiceScan
from ..naming import strip_region
from ..tags import CROSSREGION_TAG, MULTIAZ_TAG, apply_exemption, tags_to_dict
from .aws_fetch import fetch_fsx, fetch_fsx_windows_names
from .common import capture_cross_region

SERVICE = "fsx"
NAME_TAG = "Name"


def fsx_match_value(filesystem: AwsDict) -> str | None:
    """FSx ids are random, so the 'Name' tag is the only usable match value."""
    return tags_to_dict(filesystem.get("Tags")).get(NAME_TAG)


def evaluate_fsx_multiaz(filesystems: list[AwsDict], region: str) -> list[ResourceScore]:
    results: list[ResourceScore] = []
    for fs in filesystems:
        fsid = fs["FileSystemId"]
        fstype = fs.get("FileSystemType", "UNKNOWN")
        if fstype != "WINDOWS":
            reason = (f"scoring covers FSx for Windows only; this resource is FSx type "
                      f"{fstype}, recorded N/A")
            results.append(ResourceScore(SERVICE, fsid, region, None, reason))
            continue
        tags = tags_to_dict(fs.get("Tags"))
        deployment = fs.get("WindowsConfiguration", {}).get("DeploymentType", "UNKNOWN")
        if "MULTI_AZ" in deployment:
            score, reason = 100.0, f"DeploymentType is {deployment}"
        else:
            score, reason = 0.0, f"DeploymentType is {deployment} — single-AZ deployment"
        score, exempted, suffix = apply_exemption(score, tags, MULTIAZ_TAG)
        results.append(ResourceScore(SERVICE, fsid, region, score, reason + suffix, exempted))
    return results


def evaluate_fsx_crossregion(filesystems: list[AwsDict], standby_names: dict[str, set[str]],
                             primary_region: str) -> list[ResourceScore]:
    """standby_names: {standby_region: set of region-stripped Windows FSx 'Name' tags}."""
    results: list[ResourceScore] = []
    for fs in filesystems:
        fsid = fs["FileSystemId"]
        fstype = fs.get("FileSystemType", "UNKNOWN")
        if fstype != "WINDOWS":
            reason = (f"scoring covers FSx for Windows only; this resource is FSx type "
                      f"{fstype}, recorded N/A")
            results.append(ResourceScore(SERVICE, fsid, primary_region, None, reason))
            continue
        tags = tags_to_dict(fs.get("Tags"))
        raw_name = fsx_match_value(fs)
        if not raw_name:
            score = 0.0
            reason = ("no 'Name' tag to match on — FSx ids are random, so cross-region "
                      "matching requires a Name tag; add one or apply the exception tag")
        else:
            mv = strip_region(raw_name)
            hits = sorted(r for r, names in standby_names.items() if mv in names)
            if hits:
                score = 100.0
                reason = (f"name-matching heuristic: after region-stripping the Name tag "
                          f"('{mv}'), a matching Windows file system exists in {', '.join(hits)}")
            else:
                score = 0.0
                reason = (f"name-matching heuristic: no Windows file system with Name tag "
                          f"matching '{mv}' found in standby region(s) "
                          f"{', '.join(sorted(standby_names))}")
        score, exempted, suffix = apply_exemption(score, tags, CROSSREGION_TAG)
        results.append(ResourceScore(SERVICE, fsid, primary_region, score, reason + suffix, exempted))
    return results


def scan(session: Any, spec: AccountSpec) -> ServiceScan:
    primary = spec.regions[0]
    raw = fetch_fsx(session, primary)
    out = ServiceScan()
    out.multi_az = evaluate_fsx_multiaz(raw["filesystems"], primary)
    if spec.standby_regions:
        def cross_region() -> list[ResourceScore]:
            standby_names = {
                r: {strip_region(n) for n in fetch_fsx_windows_names(session, r)}
                for r in spec.standby_regions
            }
            return evaluate_fsx_crossregion(raw["filesystems"], standby_names, primary)

        capture_cross_region(out, cross_region)
        if raw["filesystems"] and not out.cross_region_error:
            out.notes_cross_region.append(ServiceNote(SERVICE, (
                "FSx has no native cross-region replication (AWS Backup copies are backups, "
                "not standby), so cross-region scoring uses the Name-tag matching heuristic")))
    return out
