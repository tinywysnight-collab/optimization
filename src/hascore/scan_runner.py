"""Per-account scan orchestration with strict N/A semantics (spec §8, §10)."""
from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .aggregation import finalize_dimension
from .models import CROSS_REGION_PATTERN, AccountResult, AccountSpec, ServiceNote
from .scanners import asg, efs, eks, elasticache, elb, fsx, msk, opensearch, rds

# Turns an account spec into a boto3-compatible session scoped to that account.
SessionFactory = Callable[[AccountSpec], Any]

SCANNERS = {
    "rds": rds.scan,
    "efs": efs.scan,
    "asg": asg.scan,
    "opensearch": opensearch.scan,
    "fsx": fsx.scan,
    "elasticache": elasticache.scan,
    "elb": elb.scan,
    "eks": eks.scan,
    "msk": msk.scan,
}


def scan_account(spec: AccountSpec, session_factory: SessionFactory) -> AccountResult:
    result = AccountResult(spec=spec)
    # Cross-Region scope is a property of the account's pattern, not of how
    # many regions the payload happens to list (spec §6).
    cross_region_scored = bool(spec.standby_regions)

    try:
        # Assuming the role is itself the access check: if it succeeds the
        # credentials are good, so no extra sts:GetCallerIdentity per account.
        session = session_factory(spec)
    except Exception as exc:  # noqa: BLE001 - any failure means inaccessible
        result.accessible = False
        result.error = f"cannot assume a role in this account: {exc}"
        return result

    # SCANNERS is read at call time so tests can patch it.
    from . import scan_runner as _self  # noqa: PLW0406 - self-import lets tests patch SCANNERS
    for name, scan_fn in _self.SCANNERS.items():
        try:
            svc = scan_fn(session, spec)
        except Exception as exc:  # noqa: BLE001 - one service failing must not kill the scan
            message = f"scan failed: {exc}; dimension recorded N/A for this service"
            result.multi_az.notes.append(ServiceNote(name, message))
            result.multi_az.failed_services.append(name)
            if cross_region_scored:
                result.cross_region.notes.append(ServiceNote(name, message))
                result.cross_region.failed_services.append(name)
            continue
        result.multi_az.resources.extend(svc.multi_az)
        result.multi_az.notes.extend(svc.notes_multi_az)
        result.cross_region.resources.extend(svc.cross_region)
        result.cross_region.notes.extend(svc.notes_cross_region)
        if svc.cross_region_error:
            result.cross_region.notes.append(ServiceNote(
                name, f"scan failed: {svc.cross_region_error}; "
                      "dimension recorded N/A for this service"))
            result.cross_region.failed_services.append(name)

    finalize_dimension(result.multi_az)
    if cross_region_scored:
        finalize_dimension(result.cross_region)
    else:
        result.cross_region.notes.append(ServiceNote(
            "all", f"pattern '{spec.pattern_id or '(none)'}' does not carry the "
                   f"{CROSS_REGION_PATTERN} marker, so no standby region is expected; "
                   "cross-region dimension recorded N/A"))
    return result


def scan_all(specs: list[AccountSpec], session_factory: SessionFactory,
             workers: int = 8) -> list[AccountResult]:
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda s: scan_account(s, session_factory), specs))
