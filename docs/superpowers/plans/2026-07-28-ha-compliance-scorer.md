# AWS HA Compliance Scorer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Python CLI (`hascore`) that scans an externally supplied list of AWS accounts and scores each account on Multi-AZ (20) and Cross-Region (20) HA compliance, with per-resource reasons, exception tags, and JSON + HTML reports.

**Architecture:** Pure-function evaluators per service (unit-tested offline, no AWS) are fed by a thin boto3 fetch layer; a scan runner orchestrates per-account scans with a thread pool and strict N/A-vs-0 semantics; reports are rendered from one JSON structure. Spec: `docs/superpowers/specs/2026-07-28-multiaz-compliance-scoring-design.md`.

**Tech Stack:** Python 3.11+, boto3, Jinja2, pytest.

**File structure:**

```
pyproject.toml
src/hascore/
  __init__.py
  models.py            # dataclasses shared by everything
  tags.py              # exception-tag (exemption) logic
  aggregation.py       # two-level score aggregation
  naming.py            # region-stripping name normalization
  input_loader.py      # account-list JSON parsing/validation
  profile_resolver.py  # ~/.aws/config profile matching
  scan_runner.py       # per-account orchestration + concurrency
  cli.py               # argparse entry point
  scanners/
    __init__.py
    aws_fetch.py       # thin boto3 wrappers (all AWS I/O lives here)
    rds.py  efs.py  asg.py  opensearch.py  fsx.py  elasticache.py  elb.py  eks.py
  report/
    __init__.py
    json_report.py
    html_report.py
    template.html.j2
tests/
  test_tags.py test_aggregation.py test_naming.py test_input_loader.py
  test_profile_resolver.py test_rds.py test_efs.py test_asg.py
  test_opensearch.py test_fsx.py test_elasticache.py test_elb.py test_eks.py
  test_scan_runner.py test_reports.py test_cli.py
```

**Conventions used throughout** (project standards come from `AGENTS.md`):
- A resource score of `None` means N/A (excluded from aggregation). `0.0` means "checked and failing". Never conflate them.
- All resource scores are on a 0–20 scale (split-scored services return one combined resource score).
- Every evaluator is a pure function: raw AWS response dicts in, `ResourceScore` list out.
- **Dependencies are managed with `uv`** (`pyproject.toml` + `uv.lock`, never requirements.txt). Run everything through `uv run`: `uv run pytest`, `uv run mypy src/`, `uv run ruff check src/`.
- **Type hints must be complete — `mypy --strict` must pass on `src/`.** Annotate every function, including `scan()` glue and boto3-facing helpers (`session` and boto3 clients are `Any`; that is expected since boto3 ships no stubs).
- **Commit messages follow `<type>(<scope>): <subject>`**, imperative mood, ≤72 chars.
- Before each commit, run `uv run pytest`, `uv run ruff check src/`, and `uv run mypy src/` — all three must be clean.
- Never commit `.idea/` or `.venv/`.

**Deliberate deviation from AGENTS.md:** the project standard says "Async: `asyncio`/`aiohttp`, never block the main thread". This tool uses `ThreadPoolExecutor` with synchronous **boto3** instead, because boto3 has no async API (async would require the third-party `aioboto3`) and the workload is I/O-bound across independent accounts, which a thread pool handles correctly. Do not convert to asyncio.

---

### Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Modify: `.gitignore` (it already contains `/.idea/` — append, do not overwrite)
- Create: `src/hascore/__init__.py`
- Create: `src/hascore/scanners/__init__.py`
- Create: `src/hascore/report/__init__.py`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "hascore"
version = "0.1.0"
description = "AWS HA compliance scorer: multi-AZ and cross-region scoring per account"
requires-python = ">=3.11"
dependencies = ["boto3>=1.34", "jinja2>=3.1"]

[project.scripts]
hascore = "hascore.cli:main"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 110
src = ["src", "tests"]

[tool.mypy]
strict = true
files = ["src"]

# boto3 ships no type stubs; its clients are intentionally Any at our boundary.
[[tool.mypy.overrides]]
module = ["boto3.*", "botocore.*"]
ignore_missing_imports = true

[dependency-groups]
dev = ["pytest>=8", "mypy>=1.11", "ruff>=0.6"]
```

- [ ] **Step 2: Append to `.gitignore`**

The file already exists and contains `/.idea/`. **Append** these lines, keeping the existing content:

```
.venv/
__pycache__/
*.pyc
out/
*.egg-info/
uv.lock
```

Note: `uv.lock` is listed here only because this tool is an internal CLI, not a published library — if the team later wants reproducible pinning committed, remove that line and commit the lock file.

- [ ] **Step 3: Create empty package files**

Create `src/hascore/__init__.py`, `src/hascore/scanners/__init__.py`, `src/hascore/report/__init__.py`, each containing nothing (empty file).

- [ ] **Step 4: Sync dependencies with uv**

Run: `uv sync`
Expected: exits 0, creates `.venv/` and `uv.lock`.

- [ ] **Step 5: Verify the toolchain runs**

Run: `uv run pytest`
Expected: "no tests ran" (exit code 5) — correct at this stage.

Run: `uv run ruff check src/`
Expected: "All checks passed!"

Run: `uv run mypy src/`
Expected: "Success: no issues found" (the package is empty, so this only proves mypy is wired up).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .gitignore src/
git commit -m "chore(scaffold): set up hascore package with uv, ruff, mypy"
```

---

### Task 2: Models and exception-tag logic

**Files:**
- Create: `src/hascore/models.py`
- Create: `src/hascore/tags.py`
- Test: `tests/test_tags.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_tags.py
from hascore.tags import MULTIAZ_TAG, apply_exemption, tags_to_dict


def test_tags_to_dict_converts_key_value_list():
    tags = [{"Key": "env", "Value": "prod"}, {"Key": "disable-multiaz", "Value": ""}]
    assert tags_to_dict(tags) == {"env": "prod", "disable-multiaz": ""}


def test_tags_to_dict_handles_none_and_empty():
    assert tags_to_dict(None) == {}
    assert tags_to_dict([]) == {}


def test_exemption_raises_failing_score_to_floor_of_10():
    score, exempted, suffix = apply_exemption(0.0, {"disable-multiaz": "true"}, MULTIAZ_TAG)
    assert score == 10.0
    assert exempted is True
    assert "disable-multiaz" in suffix


def test_exemption_tag_key_is_case_insensitive_and_value_ignored():
    score, exempted, _ = apply_exemption(0.0, {"Disable-MultiAZ": "whatever"}, MULTIAZ_TAG)
    assert (score, exempted) == (10.0, True)


def test_exemption_is_floor_not_cap():
    score, exempted, suffix = apply_exemption(20.0, {"disable-multiaz": ""}, MULTIAZ_TAG)
    assert (score, exempted, suffix) == (20.0, False, "")


def test_no_tag_no_exemption():
    score, exempted, suffix = apply_exemption(0.0, {"env": "prod"}, MULTIAZ_TAG)
    assert (score, exempted, suffix) == (0.0, False, "")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tags.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hascore.tags'`

- [ ] **Step 3: Write `src/hascore/models.py`**

```python
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
    profile: str | None = None
    profile_error: str | None = None  # set when profile resolution failed


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
```

- [ ] **Step 4: Write `src/hascore/tags.py`**

```python
"""Exception-tag (exemption) semantics: a floor of 10, never a cap."""
from __future__ import annotations

from .models import AwsDict

MULTIAZ_TAG = "disable-multiaz"
CROSSREGION_TAG = "disable-crossregion"
EXEMPT_FLOOR = 10.0


def tags_to_dict(tags: list[AwsDict] | None) -> dict[str, str]:
    """Convert an AWS [{'Key': ..., 'Value': ...}] tag list to a plain dict."""
    if not tags:
        return {}
    return {t["Key"]: t.get("Value", "") for t in tags if "Key" in t}


def apply_exemption(score: float, tags: dict[str, str], tag_key: str) -> tuple[float, bool, str]:
    """Return (final_score, exempted, reason_suffix).

    Key presence alone activates the exemption (case-insensitive); the value
    is ignored. Only scores below the floor are raised.
    """
    present = any(k.lower() == tag_key.lower() for k in tags)
    if present and score < EXEMPT_FLOOR:
        suffix = f"; exception tag '{tag_key}' present, floor raised to 10/20 per exemption rule"
        return EXEMPT_FLOOR, True, suffix
    return score, False, ""
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_tags.py -v`
Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add src/hascore/models.py src/hascore/tags.py tests/test_tags.py
git commit -m "feat(models): add data model and exception-tag exemption logic"
```

---

### Task 3: Two-level aggregation engine

**Files:**
- Create: `src/hascore/aggregation.py`
- Test: `tests/test_aggregation.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_aggregation.py
from hascore.aggregation import compute_account_score, compute_service_scores, finalize_dimension
from hascore.models import MULTI_AZ, DimensionResult, ResourceScore


def rs(service, score):
    return ResourceScore(service=service, resource_id="r", region="us-east-1", score=score, reason="x")


def test_service_scores_average_within_service():
    scores = compute_service_scores([rs("rds", 20.0), rs("rds", 0.0), rs("asg", 20.0)])
    assert scores == {"rds": 10.0, "asg": 20.0}


def test_service_with_only_na_resources_is_na():
    scores = compute_service_scores([rs("fsx", None), rs("fsx", None)])
    assert scores == {"fsx": None}


def test_na_resources_excluded_from_service_mean():
    scores = compute_service_scores([rs("elasticache", 20.0), rs("elasticache", None)])
    assert scores == {"elasticache": 20.0}


def test_account_score_equal_weight_mean_of_non_na_dimensions():
    assert compute_account_score({"rds": 10.0, "asg": 20.0, "fsx": None}) == 15.0


def test_account_score_all_na_is_na():
    assert compute_account_score({"fsx": None}) is None
    assert compute_account_score({}) is None


def test_finalize_dimension_populates_fields():
    dim = DimensionResult(MULTI_AZ, resources=[rs("rds", 20.0), rs("rds", 10.0)])
    finalize_dimension(dim)
    assert dim.service_scores == {"rds": 15.0}
    assert dim.account_score == 15.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_aggregation.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hascore.aggregation'`

- [ ] **Step 3: Write `src/hascore/aggregation.py`**

```python
"""Two-level aggregation: resource -> service dimension -> account (0-20)."""
from __future__ import annotations

from .models import DimensionResult, ResourceScore


def compute_service_scores(resources: list[ResourceScore]) -> dict[str, float | None]:
    by_service: dict[str, list[float]] = {}
    for r in resources:
        by_service.setdefault(r.service, [])
        if r.score is not None:
            by_service[r.service].append(r.score)
    return {
        svc: (round(sum(vals) / len(vals), 1) if vals else None)
        for svc, vals in by_service.items()
    }


def compute_account_score(service_scores: dict[str, float | None]) -> float | None:
    vals = [v for v in service_scores.values() if v is not None]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 1)


def finalize_dimension(dim: DimensionResult) -> None:
    dim.service_scores = compute_service_scores(dim.resources)
    dim.account_score = compute_account_score(dim.service_scores)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_aggregation.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/hascore/aggregation.py tests/test_aggregation.py
git commit -m "feat(aggregation): add two-level scoring with strict N/A semantics"
```

---

### Task 4: Region-stripping name normalization

**Files:**
- Create: `src/hascore/naming.py`
- Test: `tests/test_naming.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_naming.py
from hascore.naming import strip_region


def test_strips_embedded_region_and_collapses_separators():
    assert strip_region("myapp-ap-south-1-nodes") == "myapp-nodes"


def test_strips_leading_region():
    assert strip_region("eu-west-1-cache") == "cache"


def test_underscore_separators():
    assert strip_region("my_app_us-east-1_x") == "my_app_x"


def test_name_without_region_unchanged():
    assert strip_region("plain-name") == "plain-name"


def test_region_lookalike_inside_word_not_stripped():
    # 'eb-tier-2' inside 'web-tier-2' must not match: token boundaries required
    assert strip_region("web-tier-2") == "web-tier-2"


def test_name_that_is_only_a_region_falls_back_to_original():
    assert strip_region("us-east-1") == "us-east-1"


def test_matching_is_case_insensitive():
    assert strip_region("MyApp-US-EAST-1-nodes") == "myapp-nodes"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_naming.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hascore.naming'`

- [ ] **Step 3: Write `src/hascore/naming.py`**

```python
"""Region-stripping normalization for cross-region name matching (spec §6)."""
from __future__ import annotations

import re

# AWS region token (e.g. ap-south-1) with token boundaries so substrings of
# ordinary names (e.g. the 'eb-tier-2' inside 'web-tier-2') never match.
_REGION = re.compile(r"(?<![a-z0-9])[a-z]{2}-[a-z]+-\d(?![0-9])")
_SEP_RUN = re.compile(r"[-_.]{2,}")


def strip_region(name: str) -> str:
    lowered = name.lower()
    out = _REGION.sub("", lowered)
    out = _SEP_RUN.sub(lambda m: m.group(0)[0], out)
    out = out.strip("-_.")
    # A name that IS a region string would strip to ""; fall back to original.
    return out or lowered
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_naming.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/hascore/naming.py tests/test_naming.py
git commit -m "feat(naming): add region-stripping name normalization"
```

---

### Task 5: Input loader

**Files:**
- Create: `src/hascore/input_loader.py`
- Test: `tests/test_input_loader.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_input_loader.py
import json

import pytest

from hascore.input_loader import InputError, load_accounts


def write(tmp_path, payload):
    p = tmp_path / "accounts.json"
    p.write_text(json.dumps(payload))
    return p


def test_loads_full_account_entry(tmp_path):
    path = write(tmp_path, {"accounts": [{
        "account_id": "123456789012",
        "pattern_id": "PATTERN-A1",
        "regions": ["us-east-1", "eu-west-1"],
        "application": {"name": "payments"},
        "profile": "prod-payments",
    }]})
    specs = load_accounts(path)
    assert len(specs) == 1
    s = specs[0]
    assert s.account_id == "123456789012"
    assert s.regions == ["us-east-1", "eu-west-1"]
    assert s.pattern_id == "PATTERN-A1"
    assert s.application == {"name": "payments"}
    assert s.profile == "prod-payments"


def test_optional_fields_default(tmp_path):
    path = write(tmp_path, {"accounts": [{
        "account_id": "123456789012", "regions": ["us-east-1"],
    }]})
    s = load_accounts(path)[0]
    assert s.pattern_id is None and s.application == {} and s.profile is None


def test_rejects_bad_account_id(tmp_path):
    path = write(tmp_path, {"accounts": [{"account_id": "12345", "regions": ["us-east-1"]}]})
    with pytest.raises(InputError, match="account_id"):
        load_accounts(path)


def test_rejects_empty_regions(tmp_path):
    path = write(tmp_path, {"accounts": [{"account_id": "123456789012", "regions": []}]})
    with pytest.raises(InputError, match="regions"):
        load_accounts(path)


def test_rejects_missing_accounts_key(tmp_path):
    path = write(tmp_path, {})
    with pytest.raises(InputError, match="accounts"):
        load_accounts(path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_input_loader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hascore.input_loader'`

- [ ] **Step 3: Write `src/hascore/input_loader.py`**

```python
"""Parse and validate the externally supplied account list (spec §2)."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .models import AccountSpec

_ACCOUNT_ID = re.compile(r"^\d{12}$")


class InputError(ValueError):
    pass


def load_accounts(path: str | Path) -> list[AccountSpec]:
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or "accounts" not in data:
        raise InputError("input file must be a JSON object with an 'accounts' array")
    specs: list[AccountSpec] = []
    for i, raw in enumerate(data["accounts"]):
        account_id = raw.get("account_id", "")
        if not isinstance(account_id, str) or not _ACCOUNT_ID.match(account_id):
            raise InputError(f"accounts[{i}]: account_id must be a 12-digit string, got {account_id!r}")
        regions = raw.get("regions")
        if not isinstance(regions, list) or not regions or not all(isinstance(r, str) and r for r in regions):
            raise InputError(f"accounts[{i}]: regions must be a non-empty list of strings")
        specs.append(AccountSpec(
            account_id=account_id,
            regions=regions,
            pattern_id=raw.get("pattern_id"),
            application=raw.get("application") or {},
            profile=raw.get("profile"),
        ))
    return specs
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_input_loader.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/hascore/input_loader.py tests/test_input_loader.py
git commit -m "feat(input): add account-list loader with validation"
```

---

### Task 6: Profile resolver

**Files:**
- Create: `src/hascore/profile_resolver.py`
- Test: `tests/test_profile_resolver.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_profile_resolver.py
import pytest

from hascore.models import AccountSpec
from hascore.profile_resolver import ProfileResolutionError, load_profiles, resolve_profile

CONFIG = """
[profile prod-payments]
sso_account_id = 123456789012
sso_role_name = ReadOnly

[profile sandbox]
sso_account_id = 999999999999

[profile sandbox-admin]
sso_account_id = 999999999999

[profile no-sso]
region = us-east-1

[default]
sso_account_id = 111111111111
"""


@pytest.fixture
def mapping(tmp_path):
    cfg = tmp_path / "config"
    cfg.write_text(CONFIG)
    return load_profiles(cfg)


def test_load_profiles_maps_account_ids(mapping):
    assert mapping["123456789012"] == ["prod-payments"]
    assert mapping["111111111111"] == ["default"]
    assert sorted(mapping["999999999999"]) == ["sandbox", "sandbox-admin"]


def test_explicit_profile_wins(mapping):
    spec = AccountSpec("123456789012", ["us-east-1"], profile="override")
    assert resolve_profile(spec, mapping) == "override"


def test_single_match_resolves(mapping):
    spec = AccountSpec("123456789012", ["us-east-1"])
    assert resolve_profile(spec, mapping) == "prod-payments"


def test_no_match_raises(mapping):
    spec = AccountSpec("222222222222", ["us-east-1"])
    with pytest.raises(ProfileResolutionError, match="no profile"):
        resolve_profile(spec, mapping)


def test_multiple_matches_raise_and_name_candidates(mapping):
    spec = AccountSpec("999999999999", ["us-east-1"])
    with pytest.raises(ProfileResolutionError, match="sandbox"):
        resolve_profile(spec, mapping)


def test_uppercase_default_section_does_not_leak_into_other_profiles(tmp_path):
    """An sso_account_id in configparser's magic [DEFAULT] section must not be
    inherited by profiles that never declared it — that would resolve an account
    to a profile for a different account, silently scanning the wrong one."""
    cfg = tmp_path / "config"
    cfg.write_text("[DEFAULT]\nsso_account_id = 999999999999\n\n[profile only-profile]\nregion = us-east-1\n")
    mapping = load_profiles(cfg)
    assert mapping == {}
    with pytest.raises(ProfileResolutionError, match="no profile"):
        resolve_profile(AccountSpec("999999999999", ["us-east-1"]), mapping)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_profile_resolver.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hascore.profile_resolver'`

- [ ] **Step 3: Write `src/hascore/profile_resolver.py`**

```python
"""Match input accounts to AWS CLI profiles via ~/.aws/config (spec §3)."""
from __future__ import annotations

import configparser
from pathlib import Path

from .models import AccountSpec

# Sentinel that cannot appear as a real INI section header, so configparser's
# key-propagation behaviour is effectively disabled (see load_profiles).
_NO_DEFAULT_SECTION = "\0hascore-no-default-section"


class ProfileResolutionError(Exception):
    pass


def load_profiles(config_path: str | Path | None = None) -> dict[str, list[str]]:
    """Return {account_id: [profile names]} from sso_account_id entries."""
    path = Path(config_path) if config_path else Path.home() / ".aws" / "config"
    # configparser propagates keys from its magic default section into every
    # other section. With the stock "DEFAULT" name, an sso_account_id written
    # there would be inherited by profiles that never declared it, and a lone
    # such profile would resolve as an unambiguous match — silently scanning
    # the wrong account. Point default_section at a name no config can contain.
    parser = configparser.ConfigParser(default_section=_NO_DEFAULT_SECTION)
    parser.read(path)
    mapping: dict[str, list[str]] = {}
    for section in parser.sections():
        if section == "default":
            name = "default"
        elif section.startswith("profile "):
            name = section[len("profile "):]
        else:
            continue
        account = parser[section].get("sso_account_id")
        if account:
            mapping.setdefault(account, []).append(name)
    return mapping


def resolve_profile(spec: AccountSpec, mapping: dict[str, list[str]]) -> str:
    if spec.profile:
        return spec.profile
    matches = mapping.get(spec.account_id, [])
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ProfileResolutionError(
            f"no profile with sso_account_id={spec.account_id} in AWS config; "
            "set an explicit 'profile' for this account"
        )
    raise ProfileResolutionError(
        f"account {spec.account_id} matches multiple profiles {sorted(matches)}; "
        "set an explicit 'profile' to disambiguate"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_profile_resolver.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/hascore/profile_resolver.py tests/test_profile_resolver.py
git commit -m "feat(profile): resolve profiles via sso_account_id matching"
```

---

### Task 7: RDS evaluators

**Files:**
- Create: `src/hascore/scanners/rds.py` (evaluators only; `scan()` glue is added in Task 13)
- Test: `tests/test_rds.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_rds.py
from hascore.scanners.rds import evaluate_rds_crossregion, evaluate_rds_multiaz

R = "us-east-1"


def inst(iid, az="us-east-1a", multi_az=False, engine="mysql", replicas=(), source=None, tags=()):
    return {
        "DBInstanceIdentifier": iid,
        "AvailabilityZone": az,
        "MultiAZ": multi_az,
        "Engine": engine,
        "ReadReplicaDBInstanceIdentifiers": list(replicas),
        "ReadReplicaSourceDBInstanceIdentifier": source,
        "TagList": [{"Key": k, "Value": v} for k, v in tags],
    }


def by_id(scores):
    return {s.resource_id: s for s in scores}


# --- multi-AZ ---

def test_multiaz_enabled_scores_20():
    scores = by_id(evaluate_rds_multiaz([inst("db1", multi_az=True)], [], R))
    assert scores["db1"].score == 20.0
    assert "MultiAZ is enabled" in scores["db1"].reason


def test_cross_az_replica_scores_20_and_replica_not_scored():
    instances = [
        inst("primary", az="us-east-1a", replicas=["replica"]),
        inst("replica", az="us-east-1b", source="primary"),
    ]
    scores = by_id(evaluate_rds_multiaz(instances, [], R))
    assert set(scores) == {"primary"}
    assert scores["primary"].score == 20.0
    assert "us-east-1b" in scores["primary"].reason


def test_same_az_replica_scores_0_with_explicit_reason():
    instances = [
        inst("primary", az="us-east-1a", replicas=["replica"]),
        inst("replica", az="us-east-1a", source="primary"),
    ]
    scores = by_id(evaluate_rds_multiaz(instances, [], R))
    assert scores["primary"].score == 0.0
    assert "same" in scores["primary"].reason.lower()


def test_no_ha_scores_0_and_exemption_tag_floors_to_10():
    instances = [inst("db1"), inst("db2", tags=[("disable-multiaz", "")])]
    scores = by_id(evaluate_rds_multiaz(instances, [], R))
    assert scores["db1"].score == 0.0
    assert scores["db2"].score == 10.0 and scores["db2"].exempted


def test_aurora_scored_at_cluster_level():
    instances = [
        inst("a1", az="us-east-1a", engine="aurora-mysql"),
        inst("a2", az="us-east-1b", engine="aurora-mysql"),
        inst("solo", az="us-east-1a", engine="aurora-postgresql"),
    ]
    clusters = [
        {"DBClusterIdentifier": "c-multi", "DBClusterArn": "arn:aws:rds:us-east-1:1:cluster:c-multi",
         "DBClusterMembers": [{"DBInstanceIdentifier": "a1"}, {"DBInstanceIdentifier": "a2"}], "TagList": []},
        {"DBClusterIdentifier": "c-solo", "DBClusterArn": "arn:aws:rds:us-east-1:1:cluster:c-solo",
         "DBClusterMembers": [{"DBInstanceIdentifier": "solo"}], "TagList": []},
    ]
    scores = by_id(evaluate_rds_multiaz(instances, clusters, R))
    assert set(scores) == {"c-multi", "c-solo"}
    assert scores["c-multi"].score == 20.0
    assert scores["c-solo"].score == 0.0


# --- cross-region ---

def test_cross_region_replica_scores_20():
    arn = "arn:aws:rds:eu-west-1:111111111111:db:dr-replica"
    scores = by_id(evaluate_rds_crossregion(
        [inst("db1", replicas=[arn])], [], [], R, ["us-east-1", "eu-west-1"]))
    assert scores["db1"].score == 20.0
    assert "eu-west-1" in scores["db1"].reason


def test_cross_region_replica_outside_declared_regions_is_noted():
    arn = "arn:aws:rds:ap-south-1:111111111111:db:dr-replica"
    scores = by_id(evaluate_rds_crossregion(
        [inst("db1", replicas=[arn])], [], [], R, ["us-east-1", "eu-west-1"]))
    assert scores["db1"].score == 20.0
    assert "not in the declared regions" in scores["db1"].reason


def test_no_cross_region_replica_scores_0_and_exemption_applies():
    scores = by_id(evaluate_rds_crossregion(
        [inst("db1", tags=[("disable-crossregion", "")])], [], [], R, ["us-east-1", "eu-west-1"]))
    assert scores["db1"].score == 10.0 and scores["db1"].exempted


def test_aurora_global_database_member_scores_20():
    cluster = {"DBClusterIdentifier": "c1", "DBClusterArn": "arn:aws:rds:us-east-1:1:cluster:c1",
               "DBClusterMembers": [], "TagList": []}
    global_clusters = [{"GlobalClusterMembers": [
        {"DBClusterArn": "arn:aws:rds:us-east-1:1:cluster:c1"},
        {"DBClusterArn": "arn:aws:rds:eu-west-1:1:cluster:c1-dr"},
    ]}]
    scores = by_id(evaluate_rds_crossregion([], [cluster], global_clusters, R, ["us-east-1", "eu-west-1"]))
    assert scores["c1"].score == 20.0
    assert "eu-west-1" in scores["c1"].reason
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_rds.py -v`
Expected: FAIL — `ImportError` (module `hascore.scanners.rds` does not exist)

- [ ] **Step 3: Write `src/hascore/scanners/rds.py`**

```python
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
            # The `if az is not None` filter is redundant at runtime (the discard
            # above already removed None) but lets mypy narrow set[Any | None] to str.
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_rds.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/hascore/scanners/rds.py tests/test_rds.py
git commit -m "feat(rds): add multi-AZ and cross-region evaluators"
```

---

### Task 8: EFS evaluators

**Files:**
- Create: `src/hascore/scanners/efs.py`
- Test: `tests/test_efs.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_efs.py
from hascore.scanners.efs import evaluate_efs_crossregion, evaluate_efs_multiaz

R = "us-east-1"


def fs(fsid, one_zone=False, tags=()):
    d = {"FileSystemId": fsid, "Tags": [{"Key": k, "Value": v} for k, v in tags]}
    if one_zone:
        d["AvailabilityZoneId"] = "use1-az1"
    return d


def mt(az):
    return {"AvailabilityZoneId": az}


def by_id(scores):
    return {s.resource_id: s for s in scores}


def test_regional_with_two_az_mount_targets_scores_20():
    scores = by_id(evaluate_efs_multiaz([fs("fs-1")], {"fs-1": [mt("use1-az1"), mt("use1-az2")]}, R))
    assert scores["fs-1"].score == 20.0
    assert "Regional" in scores["fs-1"].reason and "2 AZ" in scores["fs-1"].reason


def test_regional_with_single_az_mount_target_scores_10():
    scores = by_id(evaluate_efs_multiaz([fs("fs-1")], {"fs-1": [mt("use1-az1")]}, R))
    assert scores["fs-1"].score == 10.0


def test_one_zone_scores_0():
    scores = by_id(evaluate_efs_multiaz([fs("fs-1", one_zone=True)], {"fs-1": [mt("use1-az1")]}, R))
    assert scores["fs-1"].score == 0.0
    assert "One Zone" in scores["fs-1"].reason


def test_exemption_applies_to_resource_total():
    scores = by_id(evaluate_efs_multiaz(
        [fs("fs-1", one_zone=True, tags=[("disable-multiaz", "")])], {"fs-1": []}, R))
    assert scores["fs-1"].score == 10.0 and scores["fs-1"].exempted


def test_cross_region_replication_scores_20():
    reps = [{"SourceFileSystemId": "fs-1", "Destinations": [{"Region": "eu-west-1"}]}]
    scores = by_id(evaluate_efs_crossregion([fs("fs-1")], reps, R, ["us-east-1", "eu-west-1"]))
    assert scores["fs-1"].score == 20.0
    assert "eu-west-1" in scores["fs-1"].reason


def test_same_region_replication_does_not_count():
    reps = [{"SourceFileSystemId": "fs-1", "Destinations": [{"Region": "us-east-1"}]}]
    scores = by_id(evaluate_efs_crossregion([fs("fs-1")], reps, R, ["us-east-1", "eu-west-1"]))
    assert scores["fs-1"].score == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_efs.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Write `src/hascore/scanners/efs.py`**

```python
"""EFS evaluators (spec §5.2, §6): storage 10 + mount targets 10; replication cross-region."""
from __future__ import annotations

from ..models import AwsDict, ResourceScore
from ..tags import CROSSREGION_TAG, MULTIAZ_TAG, apply_exemption, tags_to_dict

SERVICE = "efs"


def evaluate_efs_multiaz(filesystems: list[AwsDict], mount_targets_by_fs: dict[str, list[AwsDict]],
                         region: str) -> list[ResourceScore]:
    results: list[ResourceScore] = []
    for fs in filesystems:
        fsid = fs["FileSystemId"]
        tags = tags_to_dict(fs.get("Tags"))
        one_zone = bool(fs.get("AvailabilityZoneId"))
        storage_pts = 0 if one_zone else 10
        mt_azs = {mt.get("AvailabilityZoneId") or mt.get("AvailabilityZoneName")
                  for mt in mount_targets_by_fs.get(fsid, [])} - {None}
        mt_pts = 10 if len(mt_azs) >= 2 else 0
        storage_word = "One Zone" if one_zone else "Regional"
        reason = (f"{storage_word} storage class ({storage_pts}/10); "
                  f"mount targets in {len(mt_azs)} AZ(s) ({mt_pts}/10)")
        score, exempted, suffix = apply_exemption(float(storage_pts + mt_pts), tags, MULTIAZ_TAG)
        results.append(ResourceScore(SERVICE, fsid, region, score, reason + suffix, exempted))
    return results


def evaluate_efs_crossregion(filesystems: list[AwsDict], replications: list[AwsDict],
                             primary_region: str, declared_regions: list[str]) -> list[ResourceScore]:
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
        if dests:
            reg = min(dests)  # ruff FURB192: min() over sorted()[0]
            reason = f"EFS replication configured to {', '.join(sorted(dests))}"
            if reg not in declared_regions:
                reason += " (region not in the declared regions list)"
            score = 20.0
        else:
            score, reason = 0.0, "no cross-region EFS replication configuration"
        score, exempted, suffix = apply_exemption(score, tags, CROSSREGION_TAG)
        results.append(ResourceScore(SERVICE, fsid, primary_region, score, reason + suffix, exempted))
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_efs.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/hascore/scanners/efs.py tests/test_efs.py
git commit -m "feat(efs): add multi-AZ and cross-region evaluators"
```

---

### Task 9: ASG evaluators

**Files:**
- Create: `src/hascore/scanners/asg.py`
- Test: `tests/test_asg.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_asg.py
from hascore.scanners.asg import evaluate_asg_crossregion, evaluate_asg_multiaz, is_eks_asg

R = "us-east-1"


def group(name, azs, tags=()):
    return {
        "AutoScalingGroupName": name,
        "AvailabilityZones": list(azs),
        "Tags": [{"Key": k, "Value": v} for k, v in tags],
    }


def by_id(scores):
    return {s.resource_id: s for s in scores}


def test_multi_az_config_scores_20():
    scores = by_id(evaluate_asg_multiaz([group("web", ["us-east-1a", "us-east-1b"])], R))
    assert scores["web"].score == 20.0
    assert "2 AZ" in scores["web"].reason


def test_single_az_scores_0_and_exemption_floors():
    groups = [
        group("solo", ["us-east-1a"]),
        group("exempt", ["us-east-1a"], tags=[("disable-multiaz", "")]),
    ]
    scores = by_id(evaluate_asg_multiaz(groups, R))
    assert scores["solo"].score == 0.0
    assert scores["exempt"].score == 10.0 and scores["exempt"].exempted


def test_eks_origin_noted_in_reason():
    g = group("eks-ng-1234-uuid", ["us-east-1a", "us-east-1b"], tags=[("eks:cluster-name", "prod")])
    scores = by_id(evaluate_asg_multiaz([g], R))
    assert "EKS" in scores["eks-ng-1234-uuid"].reason


def test_is_eks_asg_detects_cluster_tag():
    assert is_eks_asg(group("eks-ng-1234-uuid", ["us-east-1a"], tags=[("eks:cluster-name", "prod")]))
    assert not is_eks_asg(group("plain", ["us-east-1a"]))


def test_cross_region_name_match_scores_20():
    g = group("myapp-us-east-1-web", ["us-east-1a"])
    standby = {"eu-west-1": {"myapp-web"}}
    scores = by_id(evaluate_asg_crossregion([g], standby, R))
    assert scores["myapp-us-east-1-web"].score == 20.0
    assert "heuristic" in scores["myapp-us-east-1-web"].reason
    assert "eu-west-1" in scores["myapp-us-east-1-web"].reason


def test_cross_region_no_match_scores_0():
    g = group("myapp-web", ["us-east-1a"])
    scores = by_id(evaluate_asg_crossregion([g], {"eu-west-1": {"other"}}, R))
    assert scores["myapp-web"].score == 0.0


def test_cross_region_skips_eks_node_group_asgs():
    # EKS is scored at the cluster level in its own dimension (spec §6)
    groups = [
        group("eks-40bbb26b-8679-eb64", ["us-east-1a"], tags=[("eks:cluster-name", "prod")]),
        group("myapp-web", ["us-east-1a"]),
    ]
    scores = by_id(evaluate_asg_crossregion(groups, {"eu-west-1": {"myapp-web"}}, R))
    assert set(scores) == {"myapp-web"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_asg.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Write `src/hascore/scanners/asg.py`**

```python
"""ASG evaluators (spec §5.3, §6): config-based multi-AZ; name-matching cross-region."""
from __future__ import annotations

from ..models import AwsDict, ResourceScore
from ..naming import strip_region
from ..tags import CROSSREGION_TAG, MULTIAZ_TAG, apply_exemption, tags_to_dict

SERVICE = "asg"
EKS_CLUSTER_TAG = "eks:cluster-name"


def is_eks_asg(group: AwsDict) -> bool:
    """EKS node-group ASGs are excluded from cross-region scoring: their names are
    AWS-generated random strings and EKS is matched at the cluster level (spec §6)."""
    return EKS_CLUSTER_TAG in tags_to_dict(group.get("Tags"))


def evaluate_asg_multiaz(groups: list[AwsDict], region: str) -> list[ResourceScore]:
    results: list[ResourceScore] = []
    for g in groups:
        name = g["AutoScalingGroupName"]
        tags = tags_to_dict(g.get("Tags"))
        azs = sorted(set(g.get("AvailabilityZones", [])))
        origin = f" (EKS node group ASG, cluster '{tags[EKS_CLUSTER_TAG]}')" if EKS_CLUSTER_TAG in tags else ""
        if len(azs) >= 2:
            score = 20.0
            reason = f"configuration covers {len(azs)} AZs: {', '.join(azs)}{origin}"
        else:
            score = 0.0
            reason = f"configuration covers only {len(azs)} AZ: {', '.join(azs) or 'none'}{origin}"
        score, exempted, suffix = apply_exemption(score, tags, MULTIAZ_TAG)
        results.append(ResourceScore(SERVICE, name, region, score, reason + suffix, exempted))
    return results


def evaluate_asg_crossregion(groups: list[AwsDict], standby_names: dict[str, set[str]],
                             primary_region: str) -> list[ResourceScore]:
    """standby_names: {standby_region: set of region-stripped match values}."""
    results: list[ResourceScore] = []
    for g in groups:
        if is_eks_asg(g):
            continue  # scored by the eks dimension at the cluster level
        name = g["AutoScalingGroupName"]
        tags = tags_to_dict(g.get("Tags"))
        mv = strip_region(name)
        hits = sorted(r for r, names in standby_names.items() if mv in names)
        if hits:
            score = 20.0
            reason = (f"name-matching heuristic: after region-stripping ('{mv}'), "
                      f"a matching ASG exists in {', '.join(hits)}")
        else:
            score = 0.0
            reason = (f"name-matching heuristic: no ASG matching '{mv}' found in "
                      f"standby region(s) {', '.join(sorted(standby_names))}")
        score, exempted, suffix = apply_exemption(score, tags, CROSSREGION_TAG)
        results.append(ResourceScore(SERVICE, name, primary_region, score, reason + suffix, exempted))
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_asg.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/hascore/scanners/asg.py tests/test_asg.py
git commit -m "feat(asg): add multi-AZ and cross-region evaluators"
```

---

### Task 10: OpenSearch evaluators

**Files:**
- Create: `src/hascore/scanners/opensearch.py`
- Test: `tests/test_opensearch.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_opensearch.py
from hascore.scanners.opensearch import evaluate_opensearch_crossregion, evaluate_opensearch_multiaz

R = "us-east-1"


def domain(name, za=False, az_count=1, dedicated=False, master_count=0, instance_count=1):
    cfg = {
        "ZoneAwarenessEnabled": za,
        "DedicatedMasterEnabled": dedicated,
        "InstanceCount": instance_count,
    }
    if dedicated:
        cfg["DedicatedMasterCount"] = master_count
    if za:
        cfg["ZoneAwarenessConfig"] = {"AvailabilityZoneCount": az_count}
    return {"DomainName": name, "ARN": f"arn:aws:es:us-east-1:1:domain/{name}", "ClusterConfig": cfg}


def by_id(scores):
    return {s.resource_id: s for s in scores}


def test_full_marks_needs_za_and_3az_odd_masters():
    d = domain("good", za=True, az_count=3, dedicated=True, master_count=3)
    scores = by_id(evaluate_opensearch_multiaz([d], {}, R))
    assert scores["good"].score == 20.0


def test_za_but_2az_masters_scores_10_with_quorum_reason():
    d = domain("half", za=True, az_count=2, dedicated=True, master_count=3)
    scores = by_id(evaluate_opensearch_multiaz([d], {}, R))
    assert scores["half"].score == 10.0
    assert "2 AZ" in scores["half"].reason


def test_no_za_scores_0():
    scores = by_id(evaluate_opensearch_multiaz([domain("bad")], {}, R))
    assert scores["bad"].score == 0.0


def test_no_dedicated_masters_uses_data_node_count():
    d = domain("datanodes", za=True, az_count=3, instance_count=3)
    scores = by_id(evaluate_opensearch_multiaz([d], {}, R))
    assert scores["datanodes"].score == 20.0


def test_exemption_via_tags_by_arn():
    d = domain("exempt")
    tags = {d["ARN"]: {"disable-multiaz": ""}}
    scores = by_id(evaluate_opensearch_multiaz([d], tags, R))
    assert scores["exempt"].score == 10.0 and scores["exempt"].exempted


def test_cross_region_name_match_with_connection_evidence():
    d = domain("logs-us-east-1")
    conns = [{
        "LocalDomainInfo": {"AWSDomainInformation": {"DomainName": "logs-us-east-1"}},
        "RemoteDomainInfo": {"AWSDomainInformation": {"DomainName": "logs-eu-west-1", "Region": "eu-west-1"}},
        "ConnectionStatus": {"StatusCode": "ACTIVE"},
    }]
    scores = by_id(evaluate_opensearch_crossregion([d], {}, {"eu-west-1": {"logs"}}, conns, R))
    assert scores["logs-us-east-1"].score == 20.0
    assert "heuristic" in scores["logs-us-east-1"].reason
    assert "ACTIVE cross-region connection" in scores["logs-us-east-1"].reason


def test_cross_region_no_match_scores_0():
    scores = by_id(evaluate_opensearch_crossregion(
        [domain("solo")], {}, {"eu-west-1": set()}, [], R))
    assert scores["solo"].score == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_opensearch.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Write `src/hascore/scanners/opensearch.py`**

```python
"""OpenSearch evaluators (spec §5.4, §6): data plane 10 + control plane 10; name-matching cross-region."""
from __future__ import annotations

from ..models import AwsDict, ResourceScore
from ..naming import strip_region
from ..tags import CROSSREGION_TAG, MULTIAZ_TAG, apply_exemption

SERVICE = "opensearch"


def evaluate_opensearch_multiaz(domains: list[AwsDict], tags_by_arn: dict[str, dict[str, str]],
                                region: str) -> list[ResourceScore]:
    results: list[ResourceScore] = []
    for d in domains:
        name = d["DomainName"]
        tags = tags_by_arn.get(d.get("ARN", ""), {})
        cfg = d.get("ClusterConfig", {})
        za = bool(cfg.get("ZoneAwarenessEnabled"))
        az_count = cfg.get("ZoneAwarenessConfig", {}).get("AvailabilityZoneCount", 1) if za else 1
        if cfg.get("DedicatedMasterEnabled"):
            masters = cfg.get("DedicatedMasterCount", 0)
            master_src = "dedicated masters"
        else:
            masters = cfg.get("InstanceCount", 0)
            master_src = "data nodes (no dedicated masters)"
        data_pts = 10 if za else 0
        control_ok = masters >= 3 and masters % 2 == 1 and az_count == 3
        control_pts = 10 if control_ok else 0
        reason = (f"zone awareness {'enabled' if za else 'disabled'} ({data_pts}/10); "
                  f"{masters} master-eligible {master_src} across {az_count} AZ(s)")
        if not control_ok and za:
            reason += " — a single-AZ failure may lose master quorum"
        reason += f" ({control_pts}/10)"
        score, exempted, suffix = apply_exemption(float(data_pts + control_pts), tags, MULTIAZ_TAG)
        results.append(ResourceScore(SERVICE, name, region, score, reason + suffix, exempted))
    return results


def evaluate_opensearch_crossregion(domains: list[AwsDict], tags_by_arn: dict[str, dict[str, str]],
                                    standby_domains: dict[str, set[str]], connections: list[AwsDict],
                                    primary_region: str) -> list[ResourceScore]:
    """standby_domains: {standby_region: set of region-stripped domain names}."""
    evidence: dict[str, set[str]] = {}
    for c in connections:
        remote = c.get("RemoteDomainInfo", {}).get("AWSDomainInformation", {})
        local = c.get("LocalDomainInfo", {}).get("AWSDomainInformation", {})
        status = c.get("ConnectionStatus", {}).get("StatusCode")
        if status == "ACTIVE" and remote.get("Region") and remote["Region"] != primary_region:
            evidence.setdefault(local.get("DomainName", ""), set()).add(remote["Region"])

    results: list[ResourceScore] = []
    for d in domains:
        name = d["DomainName"]
        tags = tags_by_arn.get(d.get("ARN", ""), {})
        mv = strip_region(name)
        hits = sorted(r for r, names in standby_domains.items() if mv in names)
        if hits:
            score = 20.0
            reason = (f"name-matching heuristic: after region-stripping ('{mv}'), "
                      f"a matching domain exists in {', '.join(hits)}")
        else:
            score = 0.0
            reason = (f"name-matching heuristic: no domain matching '{mv}' found in "
                      f"standby region(s) {', '.join(sorted(standby_domains))}")
        if name in evidence:
            reason += (f"; supporting evidence: ACTIVE cross-region connection(s) to "
                       f"{', '.join(sorted(evidence[name]))} (may be cross-cluster search or replication)")
        score, exempted, suffix = apply_exemption(score, tags, CROSSREGION_TAG)
        results.append(ResourceScore(SERVICE, name, primary_region, score, reason + suffix, exempted))
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_opensearch.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/hascore/scanners/opensearch.py tests/test_opensearch.py
git commit -m "feat(opensearch): add multi-AZ and cross-region evaluators"
```

---

### Task 11: FSx evaluators

**Files:**
- Create: `src/hascore/scanners/fsx.py`
- Test: `tests/test_fsx.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_fsx.py
from hascore.scanners.fsx import evaluate_fsx_crossregion, evaluate_fsx_multiaz, fsx_match_value

R = "us-east-1"


def fs(fsid, fstype="WINDOWS", deployment="MULTI_AZ_1", tags=()):
    d = {"FileSystemId": fsid, "FileSystemType": fstype,
         "Tags": [{"Key": k, "Value": v} for k, v in tags]}
    if fstype == "WINDOWS":
        d["WindowsConfiguration"] = {"DeploymentType": deployment}
    return d


def by_id(scores):
    return {s.resource_id: s for s in scores}


def test_windows_multi_az_scores_20():
    scores = by_id(evaluate_fsx_multiaz([fs("fs-1")], R))
    assert scores["fs-1"].score == 20.0
    assert "MULTI_AZ_1" in scores["fs-1"].reason


def test_windows_single_az_scores_0_and_exemption_floors():
    filesystems = [
        fs("fs-1", deployment="SINGLE_AZ_2"),
        fs("fs-2", deployment="SINGLE_AZ_2", tags=[("disable-multiaz", "")]),
    ]
    scores = by_id(evaluate_fsx_multiaz(filesystems, R))
    assert scores["fs-1"].score == 0.0
    assert scores["fs-2"].score == 10.0 and scores["fs-2"].exempted


def test_non_windows_types_are_na_with_explicit_note():
    scores = by_id(evaluate_fsx_multiaz([fs("fs-l", fstype="LUSTRE")], R))
    assert scores["fs-l"].score is None
    assert "FSx for Windows only" in scores["fs-l"].reason
    assert "LUSTRE" in scores["fs-l"].reason


# --- cross-region ---

def test_match_value_is_the_name_tag():
    assert fsx_match_value(fs("fs-1", tags=[("Name", "share-us-east-1")])) == "share-us-east-1"
    assert fsx_match_value(fs("fs-1")) is None


def test_cross_region_name_match_scores_20():
    filesystems = [fs("fs-1", tags=[("Name", "share-us-east-1")])]
    scores = by_id(evaluate_fsx_crossregion(filesystems, {"eu-west-1": {"share"}}, R))
    assert scores["fs-1"].score == 20.0
    assert "heuristic" in scores["fs-1"].reason
    assert "eu-west-1" in scores["fs-1"].reason


def test_cross_region_no_match_scores_0():
    filesystems = [fs("fs-1", tags=[("Name", "share")])]
    scores = by_id(evaluate_fsx_crossregion(filesystems, {"eu-west-1": {"other"}}, R))
    assert scores["fs-1"].score == 0.0


def test_windows_without_name_tag_scores_0_not_na():
    scores = by_id(evaluate_fsx_crossregion([fs("fs-1")], {"eu-west-1": {"share"}}, R))
    assert scores["fs-1"].score == 0.0
    assert "no 'Name' tag" in scores["fs-1"].reason


def test_cross_region_exemption_floors_to_10():
    filesystems = [fs("fs-1", tags=[("Name", "share"), ("disable-crossregion", "")])]
    scores = by_id(evaluate_fsx_crossregion(filesystems, {"eu-west-1": set()}, R))
    assert scores["fs-1"].score == 10.0 and scores["fs-1"].exempted


def test_cross_region_non_windows_is_na():
    scores = by_id(evaluate_fsx_crossregion(
        [fs("fs-l", fstype="LUSTRE", tags=[("Name", "scratch")])], {"eu-west-1": {"scratch"}}, R))
    assert scores["fs-l"].score is None
    assert "FSx for Windows only" in scores["fs-l"].reason
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_fsx.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Write `src/hascore/scanners/fsx.py`**

```python
"""FSx evaluators (spec §5.5, §6): Windows type only; other types recorded N/A."""
from __future__ import annotations

from ..models import AwsDict, ResourceScore
from ..naming import strip_region
from ..tags import CROSSREGION_TAG, MULTIAZ_TAG, apply_exemption, tags_to_dict

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
            score, reason = 20.0, f"DeploymentType is {deployment}"
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
                score = 20.0
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_fsx.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/hascore/scanners/fsx.py tests/test_fsx.py
git commit -m "feat(fsx): add multi-AZ and cross-region evaluators for Windows"
```

---

### Task 12: ElastiCache evaluators

**Files:**
- Create: `src/hascore/scanners/elasticache.py`
- Test: `tests/test_elasticache.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_elasticache.py
from hascore.scanners.elasticache import evaluate_elasticache_crossregion, evaluate_elasticache_multiaz

R = "us-east-1"


def rg(rgid, multi_az="enabled", global_id=None):
    d = {"ReplicationGroupId": rgid, "ARN": f"arn:aws:elasticache:us-east-1:1:replicationgroup:{rgid}",
         "MultiAZ": multi_az}
    if global_id:
        d["GlobalReplicationGroupInfo"] = {"GlobalReplicationGroupId": global_id}
    return d


def cc(ccid, engine="redis", rg_id=None):
    return {"CacheClusterId": ccid, "ARN": f"arn:aws:elasticache:us-east-1:1:cluster:{ccid}",
            "Engine": engine, "ReplicationGroupId": rg_id}


def by_id(scores):
    return {s.resource_id: s for s in scores}


def test_replication_group_multi_az_enabled_scores_20():
    scores = by_id(evaluate_elasticache_multiaz([rg("rg-1")], [], {}, R))
    assert scores["rg-1"].score == 20.0


def test_replication_group_multi_az_disabled_scores_0_exemption_floors():
    tags = {"arn:aws:elasticache:us-east-1:1:replicationgroup:rg-2": {"disable-multiaz": ""}}
    scores = by_id(evaluate_elasticache_multiaz(
        [rg("rg-1", multi_az="disabled"), rg("rg-2", multi_az="disabled")], [], tags, R))
    assert scores["rg-1"].score == 0.0
    assert scores["rg-2"].score == 10.0 and scores["rg-2"].exempted


def test_standalone_redis_scores_0_member_clusters_skipped():
    scores = by_id(evaluate_elasticache_multiaz(
        [rg("rg-1")], [cc("solo"), cc("member", rg_id="rg-1")], {}, R))
    assert set(scores) == {"rg-1", "solo"}
    assert scores["solo"].score == 0.0
    assert "single node" in scores["solo"].reason.lower()


def test_memcached_is_na_with_note():
    scores = by_id(evaluate_elasticache_multiaz([], [cc("mc", engine="memcached")], {}, R))
    assert scores["mc"].score is None
    assert "memcached" in scores["mc"].reason.lower()


def test_global_datastore_member_scores_20_cross_region():
    scores = by_id(evaluate_elasticache_crossregion([rg("rg-1", global_id="gd-xyz")], [], {}, R))
    assert scores["rg-1"].score == 20.0
    assert "gd-xyz" in scores["rg-1"].reason


def test_no_global_datastore_scores_0_cross_region():
    scores = by_id(evaluate_elasticache_crossregion([rg("rg-1")], [cc("solo")], {}, R))
    assert scores["rg-1"].score == 0.0
    assert scores["solo"].score == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_elasticache.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Write `src/hascore/scanners/elasticache.py`**

```python
"""ElastiCache evaluators (spec §5.6, §6): Redis/Valkey only; others N/A."""
from __future__ import annotations

from ..models import AwsDict, ResourceScore
from ..tags import CROSSREGION_TAG, MULTIAZ_TAG, apply_exemption

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
        if engine in _SCORED_ENGINES:
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
        if cluster.get("ReplicationGroupId") or cluster.get("Engine", "") not in _SCORED_ENGINES:
            continue
        ccid = cluster["CacheClusterId"]
        tags = tags_by_arn.get(cluster.get("ARN", ""), {})
        score, exempted, suffix = apply_exemption(0.0, tags, CROSSREGION_TAG)
        reason = "standalone single node, not part of any Global Datastore" + suffix
        results.append(ResourceScore(SERVICE, ccid, primary_region, score, reason, exempted))
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_elasticache.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/hascore/scanners/elasticache.py tests/test_elasticache.py
git commit -m "feat(elasticache): add multi-AZ and cross-region evaluators"
```

---

### Task 12b: ELB evaluators

Multi-AZ scores **NLB only** (spec §5.7): ALB is N/A because AWS enforces ≥2 AZs at creation, Classic/Gateway are out of scope. Cross-region covers **all types** by name-matching heuristic (spec §6).

**Files:**
- Create: `src/hascore/scanners/elb.py`
- Test: `tests/test_elb.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_elb.py
from hascore.scanners.elb import evaluate_elb_crossregion, evaluate_elb_multiaz

R = "us-east-1"


def lb(name, lb_type="application", tags=None, azs=("us-east-1a",)):
    return {"name": name, "type": lb_type, "tags": tags or {}, "azs": list(azs)}


def by_id(scores):
    return {s.resource_id: s for s in scores}


# --- multi-AZ (NLB only) ---

def test_nlb_across_two_azs_scores_20():
    scores = by_id(evaluate_elb_multiaz(
        [lb("nlb-1", lb_type="network", azs=["us-east-1a", "us-east-1b"])], R))
    assert scores["nlb-1"].score == 20.0
    assert "2 AZ" in scores["nlb-1"].reason


def test_single_az_nlb_scores_0_and_exemption_floors():
    lbs = [
        lb("nlb-solo", lb_type="network"),
        lb("nlb-exempt", lb_type="network", tags={"disable-multiaz": ""}),
    ]
    scores = by_id(evaluate_elb_multiaz(lbs, R))
    assert scores["nlb-solo"].score == 0.0
    assert scores["nlb-exempt"].score == 10.0 and scores["nlb-exempt"].exempted


def test_alb_is_na_because_aws_enforces_two_azs():
    scores = by_id(evaluate_elb_multiaz(
        [lb("alb-1", lb_type="application", azs=["us-east-1a", "us-east-1b"])], R))
    assert scores["alb-1"].score is None
    assert "enforces" in scores["alb-1"].reason


def test_classic_and_gateway_are_na():
    scores = by_id(evaluate_elb_multiaz(
        [lb("clb-1", lb_type="classic"), lb("gwlb-1", lb_type="gateway")], R))
    assert scores["clb-1"].score is None and scores["gwlb-1"].score is None
    assert "NLB only" in scores["clb-1"].reason


# --- cross-region (all types) ---

def test_name_match_scores_20_with_heuristic_reason():
    scores = by_id(evaluate_elb_crossregion(
        [lb("myapp-us-east-1-alb")], {"eu-west-1": {"myapp-alb"}}, R))
    assert scores["myapp-us-east-1-alb"].score == 20.0
    assert "heuristic" in scores["myapp-us-east-1-alb"].reason
    assert "eu-west-1" in scores["myapp-us-east-1-alb"].reason


def test_no_match_scores_0():
    scores = by_id(evaluate_elb_crossregion(
        [lb("myapp-alb")], {"eu-west-1": {"other"}}, R))
    assert scores["myapp-alb"].score == 0.0


def test_exemption_tag_floors_to_10():
    scores = by_id(evaluate_elb_crossregion(
        [lb("solo", tags={"disable-crossregion": ""})], {"eu-west-1": set()}, R))
    assert scores["solo"].score == 10.0 and scores["solo"].exempted


def test_type_appears_in_reason():
    scores = by_id(evaluate_elb_crossregion(
        [lb("legacy", lb_type="classic")], {"eu-west-1": set()}, R))
    assert "classic" in scores["legacy"].reason
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_elb.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Write `src/hascore/scanners/elb.py`**

```python
"""ELB evaluators (spec §5.7, §6): multi-AZ scores NLB only; cross-region covers all types.

Load balancers are passed as normalized dicts:
{"name": str, "type": str, "tags": dict, "azs": list[str]}
(the fetch layer merges ELBv2 and Classic ELB into this shape).
"""
from __future__ import annotations

from ..models import AwsDict, ResourceScore
from ..naming import strip_region
from ..tags import CROSSREGION_TAG, MULTIAZ_TAG, apply_exemption

SERVICE = "elb"
_SCORED_MULTIAZ_TYPE = "network"


def evaluate_elb_multiaz(load_balancers: list[AwsDict], region: str) -> list[ResourceScore]:
    results: list[ResourceScore] = []
    for lb in load_balancers:
        name, lb_type = lb["name"], lb["type"]
        if lb_type == "application":
            reason = ("AWS enforces at least two AZ subnets when an ALB is created, so there "
                      "is no configuration lever to assess; recorded N/A")
            results.append(ResourceScore(SERVICE, name, region, None, reason))
            continue
        if lb_type != _SCORED_MULTIAZ_TYPE:
            reason = (f"multi-AZ scoring covers NLB only; this is a '{lb_type}' load balancer, "
                      "recorded N/A")
            results.append(ResourceScore(SERVICE, name, region, None, reason))
            continue
        azs = sorted(set(lb.get("azs", [])))
        if len(azs) >= 2:
            score = 20.0
            reason = f"NLB is enabled in {len(azs)} AZs: {', '.join(azs)}"
        else:
            score = 0.0
            reason = f"NLB is enabled in only {len(azs)} AZ: {', '.join(azs) or 'none'}"
        score, exempted, suffix = apply_exemption(score, lb.get("tags", {}), MULTIAZ_TAG)
        results.append(ResourceScore(SERVICE, name, region, score, reason + suffix, exempted))
    return results


def evaluate_elb_crossregion(load_balancers: list[AwsDict], standby_names: dict[str, set[str]],
                             primary_region: str) -> list[ResourceScore]:
    """standby_names: {standby_region: set of region-stripped load balancer names}."""
    results: list[ResourceScore] = []
    for lb in load_balancers:
        name = lb["name"]
        mv = strip_region(name)
        hits = sorted(r for r, names in standby_names.items() if mv in names)
        if hits:
            score = 20.0
            reason = (f"name-matching heuristic: after region-stripping ('{mv}'), a matching "
                      f"{lb['type']} load balancer exists in {', '.join(hits)}")
        else:
            score = 0.0
            reason = (f"name-matching heuristic: no {lb['type']} load balancer matching '{mv}' "
                      f"found in standby region(s) {', '.join(sorted(standby_names))}")
        score, exempted, suffix = apply_exemption(score, lb.get("tags", {}), CROSSREGION_TAG)
        results.append(ResourceScore(SERVICE, name, primary_region, score, reason + suffix, exempted))
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_elb.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/hascore/scanners/elb.py tests/test_elb.py
git commit -m "feat(elb): add NLB multi-AZ and cross-region evaluators"
```

---

### Task 12c: EKS cross-region evaluator

EKS is scored at the **cluster** level in the cross-region dimension only (spec §6). Node-group ASGs are excluded from ASG cross-region scoring (Task 9) and remain scored in the Multi-AZ dimension under `asg`.

**Files:**
- Create: `src/hascore/scanners/eks.py`
- Test: `tests/test_eks.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_eks.py
from hascore.scanners.eks import evaluate_eks_crossregion

R = "ap-south-1"


def cluster(name, tags=None):
    return {"name": name, "tags": tags or {}}


def by_id(scores):
    return {s.resource_id: s for s in scores}


def test_cluster_name_match_scores_20():
    # 'abc-ap-south-1-abc' and 'abc-ap-south-2-abc' both strip to 'abc-abc'
    scores = by_id(evaluate_eks_crossregion(
        [cluster("abc-ap-south-1-abc")], {"ap-south-2": {"abc-abc"}}, R))
    assert scores["abc-ap-south-1-abc"].score == 20.0
    assert "heuristic" in scores["abc-ap-south-1-abc"].reason
    assert "ap-south-2" in scores["abc-ap-south-1-abc"].reason


def test_no_matching_cluster_scores_0():
    scores = by_id(evaluate_eks_crossregion(
        [cluster("payments-ap-south-1")], {"ap-south-2": {"billing"}}, R))
    assert scores["payments-ap-south-1"].score == 0.0
    assert "no EKS cluster matching" in scores["payments-ap-south-1"].reason


def test_exemption_tag_floors_to_10():
    scores = by_id(evaluate_eks_crossregion(
        [cluster("solo", tags={"disable-crossregion": "yes"})], {"ap-south-2": set()}, R))
    assert scores["solo"].score == 10.0 and scores["solo"].exempted


def test_multiple_standby_regions_all_listed():
    scores = by_id(evaluate_eks_crossregion(
        [cluster("abc-ap-south-1-abc")],
        {"ap-south-2": {"abc-abc"}, "eu-west-1": {"abc-abc"}}, R))
    reason = scores["abc-ap-south-1-abc"].reason
    assert "ap-south-2" in reason and "eu-west-1" in reason
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_eks.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Write `src/hascore/scanners/eks.py`**

```python
"""EKS evaluator (spec §6): cross-region dimension only, cluster-level name matching.

Clusters are passed as normalized dicts: {"name": str, "tags": dict}.
Managed node-group ASG names are AWS-generated random strings, so cross-region
matching must happen at the cluster level; this also covers Fargate-only clusters.
"""
from __future__ import annotations

from ..models import AwsDict, ResourceScore
from ..naming import strip_region
from ..tags import CROSSREGION_TAG, apply_exemption

SERVICE = "eks"


def evaluate_eks_crossregion(clusters: list[AwsDict], standby_names: dict[str, set[str]],
                             primary_region: str) -> list[ResourceScore]:
    """standby_names: {standby_region: set of region-stripped cluster names}."""
    results: list[ResourceScore] = []
    for c in clusters:
        name = c["name"]
        mv = strip_region(name)
        hits = sorted(r for r, names in standby_names.items() if mv in names)
        if hits:
            score = 20.0
            reason = (f"name-matching heuristic: after region-stripping ('{mv}'), a matching "
                      f"EKS cluster exists in {', '.join(hits)}")
        else:
            score = 0.0
            reason = (f"name-matching heuristic: no EKS cluster matching '{mv}' found in "
                      f"standby region(s) {', '.join(sorted(standby_names))}")
        score, exempted, suffix = apply_exemption(score, c.get("tags", {}), CROSSREGION_TAG)
        results.append(ResourceScore(SERVICE, name, primary_region, score, reason + suffix, exempted))
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_eks.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/hascore/scanners/eks.py tests/test_eks.py
git commit -m "feat(eks): add cluster-level cross-region evaluator"
```

---

### Task 13: AWS fetch layer and per-service scan glue

The fetch layer is the only place that talks to boto3. Each service module gains a `scan(session, spec) -> ServiceScan` function combining fetch + evaluate. Fetch functions are thin pass-throughs; `_paginate` gets a unit test, the glue is exercised end-to-end in Task 17 with fake sessions.

**Files:**
- Create: `src/hascore/scanners/aws_fetch.py`
- Modify: `src/hascore/scanners/rds.py`, `efs.py`, `asg.py`, `opensearch.py`, `fsx.py`, `elasticache.py`, `elb.py`, `eks.py` (append `scan()`)
- Test: `tests/test_aws_fetch.py`

- [ ] **Step 1: Write the failing test for `_paginate`**

```python
# tests/test_aws_fetch.py
from hascore.scanners.aws_fetch import _paginate


class FakePaginator:
    def __init__(self, pages):
        self.pages = pages

    def paginate(self, **kwargs):
        return iter(self.pages)


class FakeClient:
    def __init__(self, pages):
        self._pages = pages

    def get_paginator(self, op):
        return FakePaginator(self._pages)


def test_paginate_concatenates_pages_by_key():
    client = FakeClient([{"Items": [1, 2]}, {"Items": [3]}, {"Other": [9]}])
    assert _paginate(client, "any_op", "Items") == [1, 2, 3]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_aws_fetch.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Write `src/hascore/scanners/aws_fetch.py`**

```python
"""All boto3 I/O lives here. Every function takes a session and a region and
returns plain dicts/lists that the pure evaluators consume."""
from __future__ import annotations

from typing import Any

from ..tags import tags_to_dict


def _paginate(client: Any, op: str, result_key: str, **kwargs: Any) -> list[Any]:
    items: list[Any] = []
    for page in client.get_paginator(op).paginate(**kwargs):
        items.extend(page.get(result_key, []))
    return items


def _collect_next_token(call: Any, result_key: str, **kwargs: Any) -> list[Any]:
    """Manual NextToken paging for operations without a registered paginator."""
    items: list[Any] = []
    token: str | None = None
    while True:
        response = call(**kwargs, NextToken=token) if token else call(**kwargs)
        items.extend(response.get(result_key, []))
        token = response.get("NextToken")
        if not token:
            return items


def fetch_rds(session: Any, region: str) -> dict[str, Any]:
    c = session.client("rds", region_name=region)
    return {
        "instances": _paginate(c, "describe_db_instances", "DBInstances"),
        "clusters": _paginate(c, "describe_db_clusters", "DBClusters"),
        "global_clusters": _paginate(c, "describe_global_clusters", "GlobalClusters"),
    }


def fetch_efs(session: Any, region: str) -> dict[str, Any]:
    c = session.client("efs", region_name=region)
    filesystems = _paginate(c, "describe_file_systems", "FileSystems")
    mount_targets_by_fs = {
        fs["FileSystemId"]: _paginate(c, "describe_mount_targets", "MountTargets",
                                      FileSystemId=fs["FileSystemId"])
        for fs in filesystems
    }
    replications = _paginate(c, "describe_replication_configurations", "Replications")
    return {"filesystems": filesystems, "mount_targets_by_fs": mount_targets_by_fs,
            "replications": replications}


def fetch_asg(session: Any, region: str) -> dict[str, Any]:
    c = session.client("autoscaling", region_name=region)
    return {"groups": _paginate(c, "describe_auto_scaling_groups", "AutoScalingGroups")}


def fetch_opensearch(session: Any, region: str) -> dict[str, Any]:
    c = session.client("opensearch", region_name=region)
    names = [d["DomainName"] for d in c.list_domain_names().get("DomainNames", [])]
    domains: list[AwsDict] = []
    for i in range(0, len(names), 5):  # DescribeDomains accepts at most 5 names
        domains.extend(c.describe_domains(DomainNames=names[i:i + 5]).get("DomainStatusList", []))
    tags_by_arn = {
        d["ARN"]: tags_to_dict(c.list_tags(ARN=d["ARN"]).get("TagList", []))
        for d in domains if d.get("ARN")
    }
    # No boto3 paginator is registered for describe_outbound_connections
    # (get_paginator would raise OperationNotPageableError); page manually.
    connections = _collect_next_token(c.describe_outbound_connections, "Connections")
    return {"domains": domains, "tags_by_arn": tags_by_arn, "connections": connections}


def fetch_opensearch_domain_names(session: Any, region: str) -> list[str]:
    c = session.client("opensearch", region_name=region)
    return [d["DomainName"] for d in c.list_domain_names().get("DomainNames", [])]


def fetch_fsx(session: Any, region: str) -> dict[str, Any]:
    c = session.client("fsx", region_name=region)
    return {"filesystems": _paginate(c, "describe_file_systems", "FileSystems")}


def fetch_fsx_windows_names(session: Any, region: str) -> list[str]:
    """'Name' tag values of Windows file systems, for cross-region name matching."""
    names = []
    for fs in fetch_fsx(session, region)["filesystems"]:
        if fs.get("FileSystemType") == "WINDOWS":
            name = tags_to_dict(fs.get("Tags")).get("Name")
            if name:
                names.append(name)
    return names


def fetch_eks(session: Any, region: str) -> dict[str, Any]:
    """EKS clusters as [{'name', 'tags'}]; tags come from DescribeCluster."""
    c = session.client("eks", region_name=region)
    names = _paginate(c, "list_clusters", "clusters")
    clusters = []
    for name in names:
        described = c.describe_cluster(name=name).get("cluster", {})
        clusters.append({"name": name, "tags": described.get("tags", {}) or {}})
    return {"clusters": clusters}


def fetch_eks_cluster_names(session: Any, region: str) -> list[str]:
    c = session.client("eks", region_name=region)
    return _paginate(c, "list_clusters", "clusters")


def fetch_elb(session: Any, region: str) -> dict[str, Any]:
    """Merge ELBv2 (ALB/NLB) and Classic ELB into [{'name', 'type', 'tags'}]."""
    merged: list[AwsDict] = []

    v2 = session.client("elbv2", region_name=region)
    lbs = _paginate(v2, "describe_load_balancers", "LoadBalancers")
    for i in range(0, len(lbs), 20):  # DescribeTags accepts at most 20 ARNs
        chunk = lbs[i:i + 20]
        tag_descs = v2.describe_tags(
            ResourceArns=[lb["LoadBalancerArn"] for lb in chunk]).get("TagDescriptions", [])
        tags_by_arn = {t["ResourceArn"]: tags_to_dict(t.get("Tags", [])) for t in tag_descs}
        for lb in chunk:
            merged.append({
                "name": lb["LoadBalancerName"],
                "type": lb.get("Type", "unknown"),
                "tags": tags_by_arn.get(lb["LoadBalancerArn"], {}),
                "azs": [z["ZoneName"] for z in lb.get("AvailabilityZones", []) if z.get("ZoneName")],
            })

    classic = session.client("elb", region_name=region)
    clbs = _paginate(classic, "describe_load_balancers", "LoadBalancerDescriptions")
    for i in range(0, len(clbs), 20):  # DescribeTags accepts at most 20 names
        chunk = clbs[i:i + 20]
        tag_descs = classic.describe_tags(
            LoadBalancerNames=[lb["LoadBalancerName"] for lb in chunk]).get("TagDescriptions", [])
        tags_by_name = {t["LoadBalancerName"]: tags_to_dict(t.get("Tags", [])) for t in tag_descs}
        for lb in chunk:
            merged.append({"name": lb["LoadBalancerName"], "type": "classic",
                           "tags": tags_by_name.get(lb["LoadBalancerName"], {}),
                           "azs": lb.get("AvailabilityZones", [])})

    return {"load_balancers": merged}


def fetch_elb_names(session: Any, region: str) -> list[str]:
    v2 = session.client("elbv2", region_name=region)
    names = [lb["LoadBalancerName"] for lb in _paginate(v2, "describe_load_balancers", "LoadBalancers")]
    classic = session.client("elb", region_name=region)
    names += [lb["LoadBalancerName"]
              for lb in _paginate(classic, "describe_load_balancers", "LoadBalancerDescriptions")]
    return names


def fetch_elasticache(session: Any, region: str) -> dict[str, Any]:
    c = session.client("elasticache", region_name=region)
    groups = _paginate(c, "describe_replication_groups", "ReplicationGroups")
    clusters = _paginate(c, "describe_cache_clusters", "CacheClusters")
    tags_by_arn: dict[str, dict[str, str]] = {}
    for resource in [*groups, *clusters]:
        arn = resource.get("ARN")
        if arn:
            tags_by_arn[arn] = tags_to_dict(
                c.list_tags_for_resource(ResourceName=arn).get("TagList", []))
    return {"replication_groups": groups, "cache_clusters": clusters, "tags_by_arn": tags_by_arn}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_aws_fetch.py -v`
Expected: 1 passed

- [ ] **Step 5: Add `scan()` to each service module**

Each block below shows two things: imports to add at the top of the file, and the `scan()` function to append at the bottom. **Merge the imports into the module's existing import statements** rather than duplicating them — every scanner already has a `from ..models import AwsDict, ResourceScore` line to extend. The imports must be module-level (not inside the function) so mypy can resolve the signature.

Append to `src/hascore/scanners/rds.py`:

```python
# Add to the imports at the top of the file:
from typing import Any

from ..models import AccountSpec, ServiceScan
from .aws_fetch import fetch_rds


# Append at the end of the file:
def scan(session: Any, spec: AccountSpec) -> ServiceScan:
    primary = spec.regions[0]
    raw = fetch_rds(session, primary)
    out = ServiceScan()
    out.multi_az = evaluate_rds_multiaz(raw["instances"], raw["clusters"], primary)
    if len(spec.regions) > 1:
        out.cross_region = evaluate_rds_crossregion(
            raw["instances"], raw["clusters"], raw["global_clusters"], primary, spec.regions)
    return out
```

Append to `src/hascore/scanners/efs.py`:

```python
# Add to the imports at the top of the file:
from typing import Any

from ..models import AccountSpec, ServiceScan
from .aws_fetch import fetch_efs


# Append at the end of the file:
def scan(session: Any, spec: AccountSpec) -> ServiceScan:
    primary = spec.regions[0]
    raw = fetch_efs(session, primary)
    out = ServiceScan()
    out.multi_az = evaluate_efs_multiaz(raw["filesystems"], raw["mount_targets_by_fs"], primary)
    if len(spec.regions) > 1:
        out.cross_region = evaluate_efs_crossregion(
            raw["filesystems"], raw["replications"], primary, spec.regions)
    return out
```

Append to `src/hascore/scanners/asg.py`:

```python
# Add to the imports at the top of the file:
from typing import Any

from ..models import AccountSpec, ServiceScan
from .aws_fetch import fetch_asg


# Append at the end of the file:
def scan(session: Any, spec: AccountSpec) -> ServiceScan:
    primary = spec.regions[0]
    groups = fetch_asg(session, primary)["groups"]
    out = ServiceScan()
    out.multi_az = evaluate_asg_multiaz(groups, primary)
    if len(spec.regions) > 1:
        standby_names = {
            r: {strip_region(g["AutoScalingGroupName"])
                for g in fetch_asg(session, r)["groups"] if not is_eks_asg(g)}
            for r in spec.regions[1:]
        }
        out.cross_region = evaluate_asg_crossregion(groups, standby_names, primary)
    return out
```

Append to `src/hascore/scanners/opensearch.py`:

```python
# Add to the imports at the top of the file:
from typing import Any

from ..models import AccountSpec, ServiceScan
from .aws_fetch import fetch_opensearch, fetch_opensearch_domain_names


# Append at the end of the file:
def scan(session: Any, spec: AccountSpec) -> ServiceScan:
    primary = spec.regions[0]
    raw = fetch_opensearch(session, primary)
    out = ServiceScan()
    out.multi_az = evaluate_opensearch_multiaz(raw["domains"], raw["tags_by_arn"], primary)
    if len(spec.regions) > 1:
        standby_domains = {
            r: {strip_region(n) for n in fetch_opensearch_domain_names(session, r)}
            for r in spec.regions[1:]
        }
        out.cross_region = evaluate_opensearch_crossregion(
            raw["domains"], raw["tags_by_arn"], standby_domains, raw["connections"], primary)
    return out
```

Append to `src/hascore/scanners/fsx.py`:

```python
# Add to the imports at the top of the file:
from typing import Any

from ..models import AccountSpec, ServiceNote, ServiceScan
from .aws_fetch import fetch_fsx, fetch_fsx_windows_names


# Append at the end of the file:
def scan(session: Any, spec: AccountSpec) -> ServiceScan:
    primary = spec.regions[0]
    raw = fetch_fsx(session, primary)
    out = ServiceScan()
    out.multi_az = evaluate_fsx_multiaz(raw["filesystems"], primary)
    if len(spec.regions) > 1:
        standby_names = {
            r: {strip_region(n) for n in fetch_fsx_windows_names(session, r)}
            for r in spec.regions[1:]
        }
        out.cross_region = evaluate_fsx_crossregion(raw["filesystems"], standby_names, primary)
        if raw["filesystems"]:
            out.notes_cross_region.append(ServiceNote(SERVICE, (
                "FSx has no native cross-region replication (AWS Backup copies are backups, "
                "not standby), so cross-region scoring uses the Name-tag matching heuristic")))
    return out
```

Append to `src/hascore/scanners/eks.py`:

```python
# Add to the imports at the top of the file:
from typing import Any

from ..models import AccountSpec, ServiceScan
from .aws_fetch import fetch_eks, fetch_eks_cluster_names


# Append at the end of the file:
def scan(session: Any, spec: AccountSpec) -> ServiceScan:
    primary = spec.regions[0]
    out = ServiceScan()
    if len(spec.regions) > 1:  # EKS is scored in the cross-region dimension only (spec §6)
        clusters = fetch_eks(session, primary)["clusters"]
        standby_names = {
            r: {strip_region(n) for n in fetch_eks_cluster_names(session, r)}
            for r in spec.regions[1:]
        }
        out.cross_region = evaluate_eks_crossregion(clusters, standby_names, primary)
    return out
```

Append to `src/hascore/scanners/elb.py`:

```python
# Add to the imports at the top of the file:
from typing import Any

from ..models import AccountSpec, ServiceScan
from .aws_fetch import fetch_elb, fetch_elb_names


# Append at the end of the file:
def scan(session: Any, spec: AccountSpec) -> ServiceScan:
    primary = spec.regions[0]
    raw = fetch_elb(session, primary)
    out = ServiceScan()
    out.multi_az = evaluate_elb_multiaz(raw["load_balancers"], primary)
    if len(spec.regions) > 1:
        standby_names = {
            r: {strip_region(n) for n in fetch_elb_names(session, r)}
            for r in spec.regions[1:]
        }
        out.cross_region = evaluate_elb_crossregion(raw["load_balancers"], standby_names, primary)
    return out
```

Append to `src/hascore/scanners/elasticache.py`:

```python
# Add to the imports at the top of the file:
from typing import Any

from ..models import AccountSpec, ServiceScan
from .aws_fetch import fetch_elasticache


# Append at the end of the file:
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
```

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest`
Expected: all tests pass (no regressions from the appends)

- [ ] **Step 7: Commit**

```bash
git add src/hascore/scanners/ tests/test_aws_fetch.py
git commit -m "feat(scanners): add boto3 fetch layer and per-service scan glue"
```

---

### Task 14: Scan runner

**Files:**
- Create: `src/hascore/scan_runner.py`
- Test: `tests/test_scan_runner.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_scan_runner.py
from hascore.models import AccountSpec, ResourceScore, ServiceScan
from hascore.scan_runner import SCANNERS, scan_account, scan_all


class FakeStsClient:
    def get_caller_identity(self):
        return {"Account": "123456789012"}


class FakeSession:
    def client(self, name, region_name=None):
        return FakeStsClient()


def fake_factory(profile_name):
    return FakeSession()


def failing_factory(profile_name):
    raise RuntimeError("token expired")


def rs(service, score):
    return ResourceScore(service=service, resource_id="r1", region="us-east-1", score=score, reason="x")


def spec(regions=("us-east-1", "eu-west-1")):
    return AccountSpec("123456789012", list(regions), profile="p")


def patch_scanners(monkeypatch, mapping):
    monkeypatch.setattr("hascore.scan_runner.SCANNERS", mapping)


def test_happy_path_aggregates_both_dimensions(monkeypatch):
    patch_scanners(monkeypatch, {
        "rds": lambda session, s: ServiceScan(multi_az=[rs("rds", 20.0)], cross_region=[rs("rds", 0.0)]),
        "asg": lambda session, s: ServiceScan(multi_az=[rs("asg", 0.0)], cross_region=[rs("asg", 20.0)]),
    })
    result = scan_account(spec(), session_factory=fake_factory)
    assert result.accessible
    assert result.multi_az.account_score == 10.0
    assert result.cross_region.account_score == 10.0


def test_inaccessible_account_is_na_not_zero():
    result = scan_account(spec(), session_factory=failing_factory)
    assert not result.accessible
    assert "token expired" in result.error
    assert result.multi_az.account_score is None
    assert result.cross_region.account_score is None


def test_missing_profile_marks_inaccessible():
    s = AccountSpec("123456789012", ["us-east-1"], profile=None, profile_error="no profile found")
    result = scan_account(s, session_factory=fake_factory)
    assert not result.accessible
    assert "no profile found" in result.error


def test_service_failure_is_na_and_other_services_still_scored(monkeypatch):
    def boom(session, s):
        raise RuntimeError("AccessDenied on fsx:DescribeFileSystems")

    patch_scanners(monkeypatch, {
        "rds": lambda session, s: ServiceScan(multi_az=[rs("rds", 20.0)]),
        "fsx": boom,
    })
    result = scan_account(spec(), session_factory=fake_factory)
    assert result.multi_az.account_score == 20.0  # fsx N/A, not 0
    assert "fsx" in result.multi_az.failed_services
    assert any("AccessDenied" in n.message for n in result.multi_az.notes)


def test_single_region_account_cross_region_is_na(monkeypatch):
    patch_scanners(monkeypatch, {
        "rds": lambda session, s: ServiceScan(multi_az=[rs("rds", 20.0)]),
    })
    result = scan_account(spec(regions=("us-east-1",)), session_factory=fake_factory)
    assert result.cross_region.account_score is None
    assert any("single-region" in n.message for n in result.cross_region.notes)


def test_scan_all_returns_result_per_spec(monkeypatch):
    patch_scanners(monkeypatch, {
        "rds": lambda session, s: ServiceScan(multi_az=[rs("rds", 20.0)]),
    })
    results = scan_all([spec(), spec()], session_factory=fake_factory, workers=2)
    assert len(results) == 2


def test_scanner_registry_covers_all_eight_services():
    assert set(SCANNERS) == {"rds", "efs", "asg", "opensearch", "fsx", "elasticache", "elb", "eks"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_scan_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hascore.scan_runner'`

- [ ] **Step 3: Write `src/hascore/scan_runner.py`**

```python
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
    from . import scan_runner as _self
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_scan_runner.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/hascore/scan_runner.py tests/test_scan_runner.py
git commit -m "feat(runner): add concurrent scan runner with N/A fault tolerance"
```

---

### Task 15: JSON report

**Files:**
- Create: `src/hascore/report/json_report.py`
- Test: `tests/test_reports.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_reports.py
from hascore.models import AccountResult, AccountSpec, ResourceScore, ServiceNote
from hascore.report.json_report import build_report


def make_result():
    spec = AccountSpec("123456789012", ["us-east-1", "eu-west-1"],
                       pattern_id="P1", application={"name": "pay"})
    result = AccountResult(spec=spec)
    result.multi_az.resources = [ResourceScore("rds", "db1", "us-east-1", 20.0, "MultiAZ is enabled")]
    result.multi_az.service_scores = {"rds": 20.0}
    result.multi_az.account_score = 20.0
    result.cross_region.notes = [ServiceNote("fsx", "not scored")]
    result.cross_region.account_score = None
    return result


def test_report_shape_and_passthrough():
    report = build_report([make_result()])
    acct = report["accounts"][0]
    assert acct["account_id"] == "123456789012"
    assert acct["pattern_id"] == "P1"
    assert acct["application"] == {"name": "pay"}
    assert acct["scores"] == {"multi_az": 20.0, "cross_region": None}
    dim = acct["dimensions"]["multi_az"]
    assert dim["service_scores"] == {"rds": 20.0}
    assert dim["resources"][0]["reason"] == "MultiAZ is enabled"
    assert acct["dimensions"]["cross_region"]["notes"][0]["service"] == "fsx"


def test_summary_counts_inaccessible():
    bad = AccountResult(spec=AccountSpec("999999999999", ["us-east-1"]),
                        accessible=False, error="no profile")
    report = build_report([make_result(), bad])
    assert report["summary"]["total_accounts"] == 2
    assert report["summary"]["inaccessible_accounts"] == ["999999999999"]
    assert "generated_at" in report
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_reports.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Write `src/hascore/report/json_report.py`**

```python
"""Build the JSON report structure (the source of truth, spec §9)."""
from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from ..models import AccountResult, DimensionResult


def _dimension(dim: DimensionResult) -> dict[str, Any]:
    return {
        "account_score": dim.account_score,
        "service_scores": dim.service_scores,
        "resources": [asdict(r) for r in dim.resources],
        "notes": [asdict(n) for n in dim.notes],
        "failed_services": dim.failed_services,
    }


def _account(result: AccountResult) -> dict[str, Any]:
    spec = result.spec
    return {
        "account_id": spec.account_id,
        "pattern_id": spec.pattern_id,
        "regions": spec.regions,
        "application": spec.application,
        "profile": spec.profile,
        "accessible": result.accessible,
        "error": result.error,
        "scores": {
            "multi_az": result.multi_az.account_score,
            "cross_region": result.cross_region.account_score,
        },
        "dimensions": {
            "multi_az": _dimension(result.multi_az),
            "cross_region": _dimension(result.cross_region),
        },
    }


def build_report(results: list[AccountResult]) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": {
            "total_accounts": len(results),
            "inaccessible_accounts": [r.spec.account_id for r in results if not r.accessible],
        },
        "accounts": [_account(r) for r in results],
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_reports.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/hascore/report/json_report.py tests/test_reports.py
git commit -m "feat(report): add JSON report builder"
```

---

### Task 16: HTML report

**Files:**
- Create: `src/hascore/report/template.html.j2`
- Create: `src/hascore/report/html_report.py`
- Test: `tests/test_reports.py` (append)

- [ ] **Step 1: Append the failing tests to `tests/test_reports.py`**

```python
# append to tests/test_reports.py
from hascore.report.html_report import render_html


def test_html_contains_scores_reasons_and_metadata():
    html = render_html(build_report([make_result()]))
    assert "123456789012" in html
    assert "MultiAZ is enabled" in html
    assert "P1" in html
    assert "N/A" in html  # cross-region score for this account
    assert "<html" in html.lower()


def test_html_flags_inaccessible_accounts():
    bad = AccountResult(spec=AccountSpec("999999999999", ["us-east-1"]),
                        accessible=False, error="no profile")
    html = render_html(build_report([bad]))
    assert "999999999999" in html
    assert "inaccessible" in html.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_reports.py -v`
Expected: FAIL — `ImportError: cannot import name 'render_html'`

- [ ] **Step 3: Write `src/hascore/report/template.html.j2`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>AWS HA Compliance Report</title>
<style>
  body { font-family: -apple-system, "Segoe UI", sans-serif; margin: 2rem; color: #1a1a2e; }
  h1 { font-size: 1.5rem; } h2 { font-size: 1.2rem; margin-top: 2rem; }
  table { border-collapse: collapse; width: 100%; margin: 0.75rem 0; }
  th, td { border: 1px solid #d0d0e0; padding: 0.4rem 0.6rem; text-align: left; font-size: 0.9rem; }
  th { background: #f0f0f8; }
  .good { background: #e6f6e6; } .bad { background: #fbe9e9; }
  .partial { background: #fdf6e3; } .na { color: #888; }
  .inaccessible { background: #fbe9e9; font-weight: bold; }
  .reason { color: #444; font-size: 0.85rem; }
  details { margin: 0.5rem 0 1.5rem; }
  summary { cursor: pointer; font-weight: 600; padding: 0.3rem 0; }
</style>
</head>
<body>
<h1>AWS HA Compliance Report</h1>
<p>Generated at {{ report.generated_at }} —
{{ report.summary.total_accounts }} account(s),
{{ report.summary.inaccessible_accounts | length }} inaccessible.</p>

{% macro score_cell(value) -%}
{% if value is none %}<td class="na">N/A</td>
{% elif value >= 15 %}<td class="good">{{ value }}</td>
{% elif value >= 10 %}<td class="partial">{{ value }}</td>
{% else %}<td class="bad">{{ value }}</td>{% endif %}
{%- endmacro %}

<h2>Organization summary</h2>
<table>
<tr><th>Account</th><th>Pattern</th><th>Regions</th><th>Application</th>
    <th>Multi-AZ /20</th><th>Cross-Region /20</th></tr>
{% for a in report.accounts %}
<tr {% if not a.accessible %}class="inaccessible"{% endif %}>
  <td>{{ a.account_id }}</td>
  <td>{{ a.pattern_id or "" }}</td>
  <td>{{ a.regions | join(", ") }}</td>
  <td>{{ a.application }}</td>
  {% if a.accessible %}{{ score_cell(a.scores.multi_az) }}{{ score_cell(a.scores.cross_region) }}
  {% else %}<td colspan="2">INACCESSIBLE: {{ a.error }}</td>{% endif %}
</tr>
{% endfor %}
</table>

<h2>Account details</h2>
{% for a in report.accounts if a.accessible %}
<details>
<summary>{{ a.account_id }} — Multi-AZ:
{{ a.scores.multi_az if a.scores.multi_az is not none else "N/A" }}/20,
Cross-Region:
{{ a.scores.cross_region if a.scores.cross_region is not none else "N/A" }}/20</summary>
{% for dim_name, dim in a.dimensions.items() %}
<h3>{{ dim_name.replace("_", "-") }}</h3>
{% if dim.service_scores %}
<table>
<tr><th>Service</th><th>Dimension score /20</th></tr>
{% for svc, sc in dim.service_scores.items() %}
<tr><td>{{ svc }}</td>{{ score_cell(sc) }}</tr>
{% endfor %}
</table>
{% endif %}
{% if dim.resources %}
<table>
<tr><th>Service</th><th>Resource</th><th>Region</th><th>Score</th><th>Reason</th></tr>
{% for r in dim.resources %}
<tr><td>{{ r.service }}</td><td>{{ r.resource_id }}</td><td>{{ r.region }}</td>
{{ score_cell(r.score) }}<td class="reason">{{ r.reason }}{% if r.exempted %} [EXEMPTED]{% endif %}</td></tr>
{% endfor %}
</table>
{% endif %}
{% for note in dim.notes %}
<p class="reason">Note ({{ note.service }}): {{ note.message }}</p>
{% endfor %}
{% endfor %}
</details>
{% endfor %}
</body>
</html>
```

- [ ] **Step 4: Write `src/hascore/report/html_report.py`**

```python
"""Render the self-contained HTML report from the JSON report dict (spec §9)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATE_DIR = Path(__file__).parent


def render_html(report: dict[str, Any]) -> str:
    env = Environment(
        loader=FileSystemLoader(_TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
    )
    return env.get_template("template.html.j2").render(report=report)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_reports.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add src/hascore/report/ tests/test_reports.py
git commit -m "feat(report): add self-contained HTML report"
```

---

### Task 17: CLI and end-to-end wiring test

**Files:**
- Create: `src/hascore/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli.py
import json

from hascore.cli import main
from hascore.models import ResourceScore, ServiceScan


class FakeStsClient:
    def get_caller_identity(self):
        return {"Account": "123456789012"}


class FakeSession:
    def client(self, name, region_name=None):
        return FakeStsClient()


def fake_factory(profile_name):
    return FakeSession()


def write_inputs(tmp_path):
    accounts = tmp_path / "accounts.json"
    accounts.write_text(json.dumps({"accounts": [
        {"account_id": "123456789012", "regions": ["us-east-1", "eu-west-1"],
         "pattern_id": "P1", "application": {"name": "pay"}},
        {"account_id": "222222222222", "regions": ["us-east-1"]},
    ]}))
    aws_config = tmp_path / "aws_config"
    aws_config.write_text("[profile pay-prod]\nsso_account_id = 123456789012\n")
    return accounts, aws_config


def test_end_to_end_produces_json_and_html(tmp_path, monkeypatch):
    monkeypatch.setattr("hascore.scan_runner.SCANNERS", {
        "rds": lambda session, spec: ServiceScan(
            multi_az=[ResourceScore("rds", "db1", "us-east-1", 20.0, "MultiAZ is enabled")],
            cross_region=[ResourceScore("rds", "db1", "us-east-1", 0.0, "no cross-region read replica")],
        ),
    })
    accounts, aws_config = write_inputs(tmp_path)
    out_dir = tmp_path / "out"

    exit_code = main([str(accounts), "-o", str(out_dir), "--aws-config", str(aws_config)],
                     session_factory=fake_factory)
    assert exit_code == 0

    report = json.loads((out_dir / "report.json").read_text())
    acct1 = next(a for a in report["accounts"] if a["account_id"] == "123456789012")
    assert acct1["scores"]["multi_az"] == 20.0
    assert acct1["scores"]["cross_region"] == 0.0
    # account without a matching profile is inaccessible, not zero-scored
    acct2 = next(a for a in report["accounts"] if a["account_id"] == "222222222222")
    assert acct2["accessible"] is False
    assert acct2["scores"]["multi_az"] is None

    html = (out_dir / "report.html").read_text()
    assert "123456789012" in html and "MultiAZ is enabled" in html
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hascore.cli'`

- [ ] **Step 3: Write `src/hascore/cli.py`**

```python
"""CLI entry point: hascore <accounts.json> [-o out] [--workers N] [--aws-config PATH]."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .input_loader import load_accounts
from .profile_resolver import ProfileResolutionError, load_profiles, resolve_profile
from .report.html_report import render_html
from .report.json_report import build_report
from .scan_runner import SessionFactory, scan_all


def main(argv: list[str] | None = None,
         session_factory: SessionFactory | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hascore", description="AWS HA compliance scorer")
    parser.add_argument("input", help="path to the accounts JSON file")
    parser.add_argument("-o", "--output-dir", default="out", help="report output directory")
    parser.add_argument("--workers", type=int, default=8, help="concurrent account scans")
    parser.add_argument("--aws-config", default=None, help="override ~/.aws/config path")
    args = parser.parse_args(argv)

    specs = load_accounts(args.input)
    mapping = load_profiles(args.aws_config)
    for spec in specs:
        try:
            spec.profile = resolve_profile(spec, mapping)
        except ProfileResolutionError as exc:
            spec.profile = None
            spec.profile_error = str(exc)

    results = scan_all(specs, session_factory=session_factory, workers=args.workers)

    report = build_report(results)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, default=str))
    (out_dir / "report.html").write_text(render_html(report))

    inaccessible = report["summary"]["inaccessible_accounts"]
    print(f"Scanned {len(results)} account(s); {len(inaccessible)} inaccessible.")
    for r in results:
        maz = r.multi_az.account_score
        cr = r.cross_region.account_score
        status = "INACCESSIBLE" if not r.accessible else (
            f"multi-az={maz if maz is not None else 'N/A'} "
            f"cross-region={cr if cr is not None else 'N/A'}")
        print(f"  {r.spec.account_id}: {status}")
    print(f"Reports written to {out_dir / 'report.json'} and {out_dir / 'report.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: 1 passed

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add src/hascore/cli.py tests/test_cli.py
git commit -m "feat(cli): add entry point with end-to-end wiring test"
```

---

## Spec coverage check (for the reviewer)

| Spec section | Covered by |
|---|---|
| §2 input contract | Task 5 |
| §3 authentication / profile matching | Task 6, Task 14 (inaccessible), Task 17 (CLI resolution) |
| §4 two-level aggregation | Task 3 |
| §5.1–5.7 multi-AZ criteria | Tasks 7–12b |
| §6 cross-region criteria + name matching | Task 4 (naming), Tasks 7–12c (evaluators, incl. FSx Name-tag matching in Task 11 and cluster-level EKS in Task 12c), Task 13 (standby fetches) |
| §7 exemption tags | Task 2, exercised in every evaluator test |
| §8 N/A semantics / fault tolerance | Task 14 |
| §9 JSON + HTML output | Tasks 15–16 |
| §10 concurrency | Task 14 (`scan_all`) |
| §11 tech choices | Task 1 |
