# AWS Resilience Compliance Scoring Tool — Design

Date: 2026-07-28
Status: pending user approval

## 0. Prime directive — read-only, without exception

**The tool must never modify a scanned account.** No create, delete, modify,
update, tag, start, stop, enable, disable, or any other mutating call, under any
circumstance, for any reason — not to "fix" a finding, not to write a marker, not
to clean up after itself. A compliance scanner runs with broad reach across an
entire organization; the blast radius of a single accidental write is the whole
estate. Scanning must be an action a reviewer can approve without reading the
diff twice.

This outranks every other requirement in this document. If a future feature
cannot be built without a write call, the feature does not get built.

The rule is enforced in `tests/test_read_only_guard.py`, which parses the shipped
source and fails if any operation outside `describe_*` / `list_*` / `get_*`
appears. Documentation alone cannot hold this line; the test can.

Required permissions are correspondingly read-only: the AWS managed
`ReadOnlyAccess` or `SecurityAudit` policy is sufficient, and nothing broader
should ever be granted to this tool.

## 1. Goal

A Python library entry point that scans a caller-supplied list of AWS accounts for resilience compliance and returns two independent scores per account (each out of 20):

- **Multi-AZ score**: whether resources in the primary region have AZ-level redundancy;
- **Cross-Region score**: whether primary-region resources have a deployment/standby in another region (only multi-region accounts are scored).

Every score for every resource must carry a **specific, truthful reason** (which field was checked, what value was observed, why this score). Generic reasons like "HA not enabled" are not acceptable.

## 2. Input contract

The caller passes the account list in as a payload (a mapping, shown here as JSON):

```json
{
  "accounts": [
    {
      "account_id": "123456789012",
      "pattern_id": "PATTERN-A1",
      "regions": ["us-east-1", "eu-west-1"],
      "application": { "name": "payment-service", "owner": "team-x" },
      "profile": "optional: explicit AWS CLI profile name"
    }
  ]
}
```

- `regions[0]` is the **primary region**: Multi-AZ scoring scans the primary region only.
- `regions[1:]` are standby regions: used only by the Cross-Region dimension's name-matching scans.
- `pattern_id` and `application` are **free-schema, pass-through only**: the program does not validate or interpret them; they are copied verbatim into the output. The scoring rule engine keeps a clean interface to account metadata so pattern rules can influence scoring later (out of scope for v1).

## 3. Authentication

- The program implements no authentication logic. It relies entirely on named profiles in `~/.aws/config` after the user runs `aws sso login` (or equivalent).
- **Profile resolution**: an explicit `profile` field wins; otherwise parse `~/.aws/config` and match `sso_account_id == account_id`.
- One account matching multiple profiles → **error and require an explicit profile**; never guess.
- No matching profile / expired credentials → mark the account "inaccessible", record N/A for both scores, and continue with the remaining accounts.

## 4. Score aggregation model (two-level)

For each dimension (Multi-AZ, Cross-Region) independently:

1. **Resource → service dimension score**: arithmetic mean of all resource scores for one service within the account;
2. **Service dimension → account score**: equal-weight mean of the service dimensions that *actually have scored resources* in that account, scaled to 20.

- A service dimension with no resources is **N/A**: excluded from the mean, never drags the score down.
- With multiple regions, resources are **pooled across regions** into one service dimension (in v1 Multi-AZ scans the primary region only, so this rule matters only for future extension).
- If per-service weighting is ever needed, the equal-weight model is the all-weights=1 special case of a weighted model; the code structure supports it directly.

## 5. Multi-AZ dimension criteria (scans `regions[0]` only)

### 5.1 RDS (max 20)

- **The scoring unit is the primary instance / cluster**; read replicas are not scored separately (otherwise a single-AZ replica would score 0 and penalize the team that built HA).
- Standard instance: `MultiAZ == true` → 20; or **at least one read replica in a different AZ** → 20.
- **A same-AZ replica does not count as HA** (if the AZ dies, the replica dies with it). The reason must state this, e.g. "has 1 read replica but it shares us-east-1a with the primary; no AZ-level redundancy, score 0".
- **A replica in another region does not count either** — promoting one is a cross-region recovery (asynchronous, manual, with an endpoint change), not AZ-level redundancy, and it is already scored in the Cross-Region dimension. RDS returns such replicas as ARNs rather than bare identifiers, so they are distinguishable. The reason must say the replica exists but sits outside the region, never "no read replicas", which would send an operator to build something they already have.
- **Aurora**: the `MultiAZ` field is always false; instead check whether cluster member instances span ≥2 AZs → 20; single-instance cluster → 0.

### 5.2 EFS (two items, 10 points each)

- **Storage redundancy (10)**: Regional (`AvailabilityZoneId` empty) → 10; One Zone → 0.
- **Mount target coverage (10)**: mount targets spread across ≥2 AZs → 10; otherwise 0.
- A One Zone file system can only have 1 mount target, so its total is naturally 0. The reason explains both items separately.

### 5.3 ASG (max 20)

- **Judged by configuration** (not momentary runtime state): associated subnets/AZs cover ≥2 AZs → 20; otherwise 0.
- ASGs belonging to EKS node groups / ECS capacity providers are **not excluded** from the Multi-AZ dimension; they are scored normally with their origin noted in the reason (based on tags such as `eks:cluster-name`). AZ coverage is a genuine per-node-group configuration decision. (The Cross-Region dimension treats EKS differently — see §6.)
- Example reason: "configuration covers us-east-1a/1b/1c (3 AZs), score 20".

### 5.4 OpenSearch (max 20, single score per domain)

The split the deployment makes is who holds the master role; the score follows it.

- **With dedicated masters** (`DedicatedMasterEnabled`): master placement is AWS-managed
  (spread across three AZs on its own, even for a two-AZ domain), so only the
  **data-node spread** is the operator's decision — and it is binary:
  - data nodes span ≥2 AZs (`ZoneAwarenessEnabled`) → **20**;
  - single AZ → **0**. A healthy control plane over non-redundant data is not HA:
    quorum would be protecting a cluster that loses its data with its one AZ.
  - Legacy master counts (even, or fewer than three — the console now allows only
    3 or 5) are **advisory only**: the reason notes that OpenSearch 7.x+ keeps an
    odd voting set so even counts round down (2 acts as 1), but the score is unchanged.
- **Without dedicated masters**: the data nodes hold the master role, so their own
  AZ spread decides quorum survival:
  - 3 AZs (`ZoneAwarenessConfig.AvailabilityZoneCount == 3`) → **20**;
  - 2 AZs → **10** — a partition between the two AZs risks split-brain, and losing
    the AZ holding the majority of nodes loses quorum;
  - 1 AZ (zone awareness disabled) → **0**.
- `ZoneAwarenessEnabled` with no `ZoneAwarenessConfig` is the old-style form and
  counts as **2 AZs**, not one.
- **Known blind spots, deliberately not detected**: AWS's automatic three-AZ master
  placement does not hold in regions with only two AZs (e.g. `us-west-1`) or with
  older instance types unavailable in three AZs — neither is visible in
  `DescribeDomains`. Index replica counts live in the data-plane API and are not
  checked; a domain whose indexes have zero replicas can still score 20.

### 5.5 FSx (max 20, Windows type only)

- **FSx for Windows**: `DeploymentType` contains `MULTI_AZ` → 20; SINGLE_AZ types → 0.
- **Lustre / ONTAP / OpenZFS**: recorded as **N/A**, excluded from scoring, but each resource is listed in the report with an explicit note: "scoring covers FSx for Windows only; this resource is FSx for XXX, recorded N/A".

### 5.6 ElastiCache (max 20, Redis/Valkey only)

- **Redis/Valkey replication group**: `MultiAZ == enabled` → 20; otherwise 0.
- **Standalone Redis node (no replication group)**: 0, reason "single node, no replica".
- **Memcached / Serverless / other forms**: recorded as **N/A**, listed in the report with a note that they are excluded from scoring.

### 5.7 ELB (max 20, NLB only)

- **Network Load Balancer**: enabled AZs ≥2 → 20; a single AZ → 0. Judged by configuration (`AvailabilityZones` from `DescribeLoadBalancers`), consistent with ASG.
- **Application Load Balancer**: recorded as **N/A**. AWS enforces at least two AZ subnets when an ALB is created, so there is no configuration lever to assess — scoring it would add a permanently-full-marks dimension that dilutes real failures elsewhere. Same principle as FSx Lustre in §5.5.
- **Classic ELB and Gateway Load Balancer**: recorded as **N/A**, listed in the report with a note that scoring covers NLB only.

## 6. Cross-Region dimension criteria (max 20, independent of the Multi-AZ score)

- Scored only when the input has ≥2 `regions`; **single-region accounts are N/A** (not 0).
- Aggregation model identical to Multi-AZ (two-level, N/A dimensions excluded).

### Scored services and detection

| Service | Detection | Full score condition |
|---|---|---|
| RDS | Native API: cross-region read replica (replica ARN in another region) / Aurora Global Database | exists → 20 |
| EFS | Native API: replication configuration targeting another region | exists → 20 |
| ElastiCache Redis | Native API: Global Datastore membership | exists → 20 |
| ASG (non-EKS only) | **Name-matching heuristic**: same name after region-stripping exists in a standby region | match → 20 |
| EKS | **Name-matching heuristic**: same cluster name after region-stripping exists in a standby region (via `ListClusters`) | match → 20 |
| OpenSearch | **Name-matching heuristic**: same domain name after region-stripping exists in a standby region | match → 20 |
| ELB (ALB/NLB/Classic) | **Name-matching heuristic**: same load balancer name after region-stripping exists in a standby region | match → 20 |
| FSx for Windows | **Name-matching heuristic**: same `Name` tag after region-stripping exists on a Windows file system in a standby region | match → 20 |

- **EKS is judged at the cluster level, directly through the EKS API**, and forms its own service dimension. Node-group ASGs are *excluded* from the ASG cross-region scoring (they remain scored in the Multi-AZ dimension) for three reasons: managed node-group ASG names are AWS-generated random strings (`eks-40bbb26b-…`) that can never match across regions; node-group names are commonly generic (`default`, `spot`, `system`) and would produce false positives against unrelated clusters in a standby region; and a cluster with four node groups would otherwise carry four times the weight of a single-node-group cluster. Cluster-level matching also covers Fargate-only clusters, which have no ASG at all. Exemption tags are read from the cluster's own tags.
- **ELB cross-region scoring covers all load balancer types** (ALB, NLB, Classic), unlike the Multi-AZ dimension which scores NLB only (§5.7) — a missing DR copy is a real gap regardless of load balancer type. The match value is the load balancer name.
- **FSx for Windows** has no native cross-region replication (AWS Backup copies are backups, not standby; DataSync sync is invisible from the FSx API), so it is scored with the name-matching heuristic over the **`Name` tag** (file system ids are random, so the tag is the only usable match value). A Windows file system **without a `Name` tag scores 0** with a reason explaining there is no name to match on — N/A would let untagged resources escape scoring. Other FSx types remain N/A.
- Native-relation detection: a standby in *any* other region counts; if that region is not in the input `regions` list, the reason says so.
- Name-matching scans only the standby regions declared in the input.

### Name-matching rule (shared by ASG, EKS, OpenSearch, ELB, and FSx for Windows)

1. Pick the match value: ASG → the ASG name (EKS node-group ASGs, identified by the `eks:cluster-name` tag, are skipped entirely); EKS → the cluster name; OpenSearch → the domain name; ELB → the load balancer name; FSx for Windows → the `Name` tag.
2. **Strip region substrings**: delete substrings matching the AWS region pattern (regex `[a-z]{2}-[a-z]+-\d`, e.g. `ap-south-1`), then collapse leftover consecutive separators (`--`, `__`, etc.) into one.
3. **Exact match** after stripping; no fuzzy rules. A name match counts as multi-region deployment; **node counts and configuration are not compared**.
4. The reason must state the heuristic nature, e.g. "name-matching heuristic: after region-stripping, matches ASG `myapp-nodes` in eu-west-1". For OpenSearch, if an ACTIVE cross-region connection is also detected (`DescribeOutboundConnections`), it is recorded in the reason as supporting evidence, but the verdict is based on the name match.

## 7. Exception (exemption) mechanism

- Each dimension has its own independent tag; they do not affect each other:
  - Multi-AZ: tag key = `disable-multiaz`
  - Cross-Region: tag key = `disable-crossregion`
- **Key presence alone activates the exemption; the value is ignored.**
- Semantics are a floor, not a cap: final resource score = `max(actual score, 10)`. A resource that passes its check still gets 20.
- For split-scored services (EFS, OpenSearch): the exemption applies to the **resource total** (`max(sum of items, 10)`), not per item.
- Reason wording: "multi-AZ not enabled, but exception tag `disable-multiaz` present; 10/20 per exemption rule".
- **No account-level exemption** (out of scope for v1; if needed, handle via an allowlist at the report layer, never inside the scoring engine).

## 8. N/A semantics and fault tolerance

Core principle: **"not scanned" and "scanned and found bad" are never conflated; N/A and 0 stay strictly separate.**

| Situation | Handling |
|---|---|
| No profile / expired credentials / cannot access account | Account marked "inaccessible", both scores N/A, listed prominently in the report, scan continues |
| A service API call fails (e.g. missing `fsx:Describe*` permission) | That service dimension marked "scan failed", N/A; other services scored normally; reason records the error |
| Account has none of a resource type | Service dimension N/A, excluded from the mean |
| Resource type out of scoring scope (Lustre, Memcached, ...) | Listed per resource in the report with explanation, excluded from scoring |
| Single-region account | Cross-Region score N/A |

## 9. Output

- **JSON (source of truth)**: org summary → per account (both scores, inaccessible flag) → per service dimension score → per resource (score, reason, region, exemption flag, pass-through `pattern_id` / `application`).
- **HTML (human report)**: single self-contained file (no external resources). Structure: org summary table (both scores per account) → account detail (service dimension scores) → resource detail (scores and reasons). Shows `pattern_id` / application details, inaccessible accounts, and the out-of-scope resource list.
- Run mode: **a single callable entry point**, `score(payload, output_format)`, returning the result rather than writing files:
  - `output_format="json"` returns the report as a dict; the caller decides whether to serialize it.
  - `output_format="html"` returns a self-contained HTML document as a string.
  - Both formats render from the same scan; `render_html(report)` produces HTML from an already-returned JSON report without rescanning.
- No command-line interface, no reading the payload from disk, no writing report files — the caller owns all I/O. Trend tracking and persistence are out of scope for v1.

## 10. Scale and concurrency

- Scale: a few hundred accounts × 1–2 regions.
- Account-level concurrency (default 8 workers, configurable); services scanned sequentially within an account; automatic backoff/retry on API throttling (boto3 adaptive retry).
- Estimate: <30 s per account; ~300 accounts at concurrency 8 finish in roughly 20–30 minutes.

## 11. Technology choices

- **Python 3.11+ / boto3**, imported as a library (`from hascore import score`).
- Required AWS permissions: read-only `Describe*/List*` (covered by `ReadOnlyAccess` or `SecurityAudit`).
- HTML rendered with a template engine (Jinja2); JSON is the primary artifact, HTML is rendered from the same data.
- Testing: pytest; the AWS API layer is wrapped behind injectable interfaces; the scoring engine is pure functions, unit-testable offline.

## 12. Explicitly out of scope for v1 (YAGNI list)

- Account-level exemptions (allowlist)
- ALB / Classic ELB / Gateway LB multi-AZ scoring (only NLB is scored in the Multi-AZ dimension)
- EKS multi-AZ scoring as its own dimension (node-group AZ coverage is already scored under ASG; EKS control-plane AZ spread is AWS-managed and has no configuration lever)
- `pattern_id` influencing scores (interface reserved)
- OpenSearch data-plane checks (index replica counts), reading cross-cluster replication rules
- Network topology checks beyond EFS mount targets
- Trend tracking / historical comparison / persistence
- Auto-enumerating Organizations accounts (the list is supplied externally)
- Multi-AZ scanning outside the primary region
