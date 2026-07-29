"""Shared data model for the HA compliance scorer."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MULTI_AZ = "multi_az"
CROSS_REGION = "cross_region"

# Raw AWS API response objects. boto3 ships no type stubs, so their shape is Any.
AwsDict = dict[str, Any]


@dataclass
class AccountSpec:
    """One entry from the externally supplied account list."""
    account_id: str
    regions: list[str]
    pattern_id: str | None = None
    application: dict[str, Any] = field(default_factory=dict)
    role_name: str | None = None  # overrides the global role for this account


@dataclass
class ResourceScore:
    service: str          # "rds", "efs", "asg", "opensearch", "fsx", "elasticache"
    resource_id: str
    region: str
    score: float | None   # 0-20; None = N/A (excluded from aggregation)
    reason: str
    exempted: bool = False


@dataclass
class ServiceNote:
    """Non-score information surfaced in the report (scan failures, scope notes)."""
    service: str
    message: str


@dataclass
class DimensionResult:
    name: str  # MULTI_AZ or CROSS_REGION
    resources: list[ResourceScore] = field(default_factory=list)
    notes: list[ServiceNote] = field(default_factory=list)
    failed_services: list[str] = field(default_factory=list)
    service_scores: dict[str, float | None] = field(default_factory=dict)
    account_score: float | None = None


@dataclass
class AccountResult:
    spec: AccountSpec
    accessible: bool = True
    error: str | None = None
    multi_az: DimensionResult = field(default_factory=lambda: DimensionResult(MULTI_AZ))
    cross_region: DimensionResult = field(default_factory=lambda: DimensionResult(CROSS_REGION))


@dataclass
class ServiceScan:
    """What one service scanner returns for one account."""
    multi_az: list[ResourceScore] = field(default_factory=list)
    cross_region: list[ResourceScore] = field(default_factory=list)
    notes_multi_az: list[ServiceNote] = field(default_factory=list)
    notes_cross_region: list[ServiceNote] = field(default_factory=list)
