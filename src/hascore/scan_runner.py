"""Per-account scan orchestration with strict N/A semantics (spec §8, §10)."""
from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .aggregation import finalize_dimension
from .models import AccountResult, AccountSpec, ServiceNote
from .scanners import asg, efs, eks, elasticache, elb, fsx, opensearch, rds

# Builds a boto3-compatible session from a profile name.
SessionFactory = Callable[..., Any]

SCANNERS = {
    "rds": rds.scan,
    "efs": efs.scan,
    "asg": asg.scan,
    "opensearch": opensearch.scan,
    "fsx": fsx.scan,
    "elasticache": elasticache.scan,
    "elb": elb.scan,
    "eks": eks.scan,
}


def _default_session_factory(profile_name: str) -> Any:
    import boto3
    return boto3.Session(profile_name=profile_name)


def scan_account(spec: AccountSpec, session_factory: SessionFactory | None = None) -> AccountResult:
    factory = session_factory or _default_session_factory
    result = AccountResult(spec=spec)
    multi_region = len(spec.regions) > 1

    if not spec.profile:
        result.accessible = False
        result.error = spec.profile_error or "no AWS profile resolved for this account"
        return result

    try:
        session = factory(profile_name=spec.profile)
        session.client("sts", region_name=spec.regions[0]).get_caller_identity()
    except Exception as exc:  # noqa: BLE001 - any failure means inaccessible
        result.accessible = False
        result.error = f"cannot access account with profile '{spec.profile}': {exc}"
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
            if multi_region:
                result.cross_region.notes.append(ServiceNote(name, message))
                result.cross_region.failed_services.append(name)
            continue
        result.multi_az.resources.extend(svc.multi_az)
        result.multi_az.notes.extend(svc.notes_multi_az)
        result.cross_region.resources.extend(svc.cross_region)
        result.cross_region.notes.extend(svc.notes_cross_region)

    finalize_dimension(result.multi_az)
    if multi_region:
        finalize_dimension(result.cross_region)
    else:
        result.cross_region.notes.append(ServiceNote(
            "all", "single-region account; cross-region dimension recorded N/A"))
    return result


def scan_all(specs: list[AccountSpec], session_factory: SessionFactory | None = None,
              workers: int = 8) -> list[AccountResult]:
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda s: scan_account(s, session_factory), specs))
