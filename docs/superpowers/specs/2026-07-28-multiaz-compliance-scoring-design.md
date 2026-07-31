# AWS Resilience Compliance Scoring Tool — Design

Date: 2026-07-28
Status: implemented (v1)

**Source of truth.** Behaviour changes here first, then in code, both in the same
commit (see the spec-first workflow in `AGENTS.md`). Where this and `src/`
disagree, this document is the defect.

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

A Python library entry point that scans a caller-supplied list of AWS accounts for resilience compliance and returns two independent scores per account (each 0–100):

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
      "environment": "production",
      "regions": ["us-east-1", "eu-west-1"],
      "application": { "name": "payment-service", "owner": "team-x" },
      "role_name": "optional: overrides the role assumed in this account"
    }
  ]
}
```

- The payload must be a mapping whose `accounts` is an **array of mappings** — malformed containers or account entries raise `InputError` rather than leaking `AttributeError` or silently scanning zero accounts.
- `regions[0]` is the **primary region**: Multi-AZ scoring scans the primary region only.
- `regions[1]` is the **standby region**, used only by the Cross-Region dimension and only for accounts in its scope (§6).
- Region values must be non-empty strings, **names botocore recognises**, and **unique** within an account. An unknown region is rejected at parse time, where it reads as the payload error it is, rather than later as a wall of per-service endpoint failures on that account. The check uses botocore's region list across every partition it ships, not `get_partition_for_region`, which only pattern-matches and so accepts a plausible typo like `ap-south-99`. The message names a stale `boto3` as the other cause when the region is genuinely new. An account whose `pattern_id` carries the Cross-Region marker must list **exactly two distinct regions** — primary, then standby. One region, a duplicated region, and three or more regions are **rejected at parse time**: the pattern names one primary and one standby, so a payload that disagrees is a contradiction to surface immediately rather than paper over by ignoring the extras, and failing fast beats discovering it after scanning hundreds of accounts. Accounts outside the pattern may list any number of distinct regions.
- `pattern_id` is an optional string, copied into the output. Two markers in it change scope; any other content is uninterpreted. Both match as a **substring, case-insensitively**:
  - **`GS-001`** — the account runs a designated standby. Cross-Region is scored (§6), and the payload must list exactly two distinct regions.
  - **`PTM`** — the account runs **several mutually independent regions**. Multi-AZ is assessed in **every** listed region; Cross-Region is **N/A**, because independent deployments are not standbys for one another. Any number of regions is accepted, including one.
  - A `pattern_id` carrying **both** markers is **rejected at parse time**: an account cannot both pair one standby and run independent regions, and silently honouring one marker would hide the contradiction.
- `application` is optional arbitrary JSON metadata. It is copied **verbatim** into JSON output and rendered safely in HTML; when omitted it defaults to an empty mapping.
- `environment` is an optional string (`production`, `uat`, `dev`, whatever the caller uses) shown in the report so a reader can tell which estate a row belongs to. It is **display-only**: uninterpreted, never scored, and never a scope gate. Values are not validated against a fixed list — the caller owns the vocabulary.

## 3. Access model — one master identity, a role assumed per account

- The caller supplies **one master-account profile**; that identity assumes a role
  in every target account. Nothing else is configured per account.
- **`role_name` is configurable** and defaults to `OrganizationAccountAccessRole`,
  because organizations name the audit role differently. An account entry may
  carry its own `role_name` when it is the odd one out; the global value applies
  to the rest.
- `master_profile` names a profile in the caller's AWS config. Omitting it falls
  back to the default credential chain, which is what an EC2 instance or ECS task
  already running in the master account carries.
- `external_id` is sent on the assume call only when configured, for trust
  policies that require it. `session_name` defaults to `hascore-resilience-scan`
  so the assumed sessions are identifiable in CloudTrail.
- The role must exist in each account and trust the master identity. It needs
  **read-only** permissions: `ReadOnlyAccess` or `SecurityAudit` is sufficient.
- The target role ARN uses the AWS partition resolved from the account's primary
  Region (`aws`, `aws-cn`, `aws-us-gov`, ISO partitions, ...); it is never
  hard-coded to `arn:aws`. Because STS delegation cannot cross partitions, every
  account in one invocation must be reachable from the supplied master identity
  in its own partition; otherwise that account is reported inaccessible.
- **`sts:AssumeRole` vends temporary credentials and modifies nothing**, so it
  does not breach the prime directive in §0. The read-only guard lists it as an
  explicit, reviewed exemption alongside `sts:GetCallerIdentity`.
- The assume call **is** the access check: succeeding proves the credentials work,
  so no extra `GetCallerIdentity` is spent per account.
- One STS client is built from the master session and shared by every worker
  thread — botocore clients are thread-safe for calls, while sessions are not
  safe to construct concurrently.
- An account whose role cannot be assumed (missing role, untrusting policy,
  expired master credentials, suspended account) is marked **inaccessible** with
  N/A for both scores; the scan continues with the remaining accounts.

## 4. Score aggregation model (two-level)

**Every score is 0–100**, at the resource level and at both levels of
aggregation. The unit is the same one the final organization-wide score is
reported in, so a roll-up across many dimensions is a weighted mean with no
rescaling step and no intermediate total to keep in step with the dimension
count. Granularity stays coarse on purpose — the underlying judgements are
pass / partial / fail, so scores land on **100 / 50 / 0** rather than implying a
precision the checks do not have.


For each dimension (Multi-AZ, Cross-Region) independently:

1. **Resource → service dimension score**: arithmetic mean of all resource scores for one service within the account;
2. **Service dimension → account score**: equal-weight mean of the service dimensions that *actually have scored resources* in that account.

- A service dimension with no resources is **N/A**: excluded from the mean, never drags the score down.
- **The account score is the only score that ranks an account.** When a dimension covers more than one region (a `PTM` account, §2), the report additionally breaks each service dimension down **per region** — `{"rds": {"ap-south-1": 100, "eu-west-1": 0}}`. This is **display only**: it locates which region dragged a pooled score down, and never feeds back into the account number. It is omitted when only one region was scanned, where it would just restate the service scores.
- With multiple regions, resources are **pooled across regions** into one service dimension (in v1 Multi-AZ scans the primary region only, so this rule matters only for future extension).
- If per-service weighting is ever needed, the equal-weight model is the all-weights=1 special case of a weighted model; the code structure supports it directly.

## 5. Multi-AZ dimension criteria

**Which regions are scanned depends on the pattern.** A `PTM` account runs
independent deployments, so each of its regions is assessed on its own merits;
every other account is scanned in `regions[0]` only, since a `GS-001` standby is
a copy rather than a separate estate.

Resources from every scanned region **pool into one service dimension** per §4 —
an account gets one `rds` score, not one per region — and each resource carries
its own region in the report so a failure can be located.

### 5.1 RDS (0–100)

- **The scoring unit is the primary instance / cluster**; read replicas are not scored separately (otherwise a single-AZ replica would score 0 and penalize the team that built HA).
- Standard instance: `MultiAZ == true` → 100; or **at least one read replica in a different AZ** → 100.
- **A same-AZ replica does not count as HA** (if the AZ dies, the replica dies with it). The reason must state this, e.g. "has 1 read replica but it shares us-east-1a with the primary; no AZ-level redundancy, score 0".
- **A replica in another region does not count either** — promoting one is a cross-region recovery (asynchronous, manual, with an endpoint change), not AZ-level redundancy, and it is already scored in the Cross-Region dimension. RDS returns such replicas as ARNs rather than bare identifiers, so they are distinguishable. The reason must say the replica exists but sits outside the region, never "no read replicas", which would send an operator to build something they already have.
- **Aurora**: the `MultiAZ` field is always false; instead check whether cluster member instances span ≥2 AZs → 100; single-instance cluster → 0.
- **RDS Multi-AZ DB cluster**: `DescribeDBClusters` returns these non-Aurora
  MySQL/PostgreSQL clusters alongside Aurora. Score the cluster once, never its
  member instances: `MultiAZ == true` and member instances resolved across ≥2 AZs
  → 100; otherwise → 0 with the missing condition named. A cluster whose
  `ReplicationSourceIdentifier` is set is a read replica and is not scored
  separately.

### 5.2 EFS (two items, 50 points each)

- **Storage redundancy (50)**: Regional (`AvailabilityZoneId` empty) → 10; One Zone → 0.
- **Mount target coverage (50)**: mount targets spread across ≥2 AZs → 10; otherwise 0. AZ identity is taken from `AvailabilityZoneId` for the whole set, falling back to `AvailabilityZoneName` only when no mount target carries an ID — mixing the two field kinds would count one physical AZ twice and produce a false pass.
- A One Zone file system can only have 1 mount target, so its total is naturally 0. The reason explains both items separately.

### 5.3 ASG (0–100)

- **Judged by configuration** (not momentary runtime state): associated subnets/AZs cover ≥2 AZs → 100; otherwise 0.
- ASGs belonging to EKS node groups / ECS capacity providers are **not excluded** from the Multi-AZ dimension; they are scored normally with their origin noted in the reason (based on tags such as `eks:cluster-name`). AZ coverage is a genuine per-node-group configuration decision. (The Cross-Region dimension treats EKS differently — see §6.)
- Example reason: "configuration covers us-east-1a/1b/1c (3 AZs), score 100".

### 5.4 OpenSearch (0–100, single score per domain)

The split the deployment makes is who holds the master role; the score follows it.

- **With dedicated masters** (`DedicatedMasterEnabled`): master placement is AWS-managed
  (spread across three AZs on its own, even for a two-AZ domain), so only the
  **data-node spread** is the operator's decision — and it is binary:
  - data nodes span ≥2 AZs (`ZoneAwarenessEnabled`) → **100**;
  - single AZ → **0**. A healthy control plane over non-redundant data is not HA:
    quorum would be protecting a cluster that loses its data with its one AZ.
  - Legacy master counts (even, or fewer than three — the console now allows only
    3 or 5) are **advisory only**: the reason notes that OpenSearch 7.x+ keeps an
    odd voting set so even counts round down (2 acts as 1), but the score is unchanged.
- **Without dedicated masters**: the data nodes hold the master role, so their own
  AZ spread decides quorum survival:
  - 3 AZs (`ZoneAwarenessConfig.AvailabilityZoneCount == 3`) → **100**;
  - 2 AZs → **50** — a partition between the two AZs risks split-brain, and losing
    the AZ holding the majority of nodes loses quorum;
  - 1 AZ (zone awareness disabled) → **0**.
- `ZoneAwarenessEnabled` with no `ZoneAwarenessConfig` is the old-style form and
  counts as **2 AZs**, not one.
- **Stated environment assumption**: every region this estate scans has at least
  three AZs. AWS's automatic three-AZ master placement depends on three existing,
  so in a two-AZ region (`us-west-1`) masters would land 2+1 and a dedicated-master
  domain there would score 100 while carrying a documented "50/50 chance of
  downtime". **Adding a two-AZ region to the payload breaks this assumption** and
  the check would then need an EC2 `DescribeAvailabilityZones` call.
- **Known blind spots, deliberately not detected**: an older-generation instance
  type unavailable in three AZs also forces masters into two zones — but only for a
  domain that selected **two** AZs, since a three-AZ domain on such a type fails at
  creation. The inputs are visible (`ClusterConfig.DedicatedMasterType`); what is
  missing is a maintained list of legacy types. Index replica counts live in the
  data-plane API and are not checked; a domain whose indexes have zero replicas can
  still score 100.

### 5.5 FSx (0–100, Windows type only)

- **FSx for Windows**: `DeploymentType` contains `MULTI_AZ` → 100; SINGLE_AZ types → 0.
- **Lustre / ONTAP / OpenZFS**: recorded as **N/A**, excluded from scoring, but each resource is listed in the report with an explicit note: "scoring covers FSx for Windows only; this resource is FSx for XXX, recorded N/A".

### 5.6 ElastiCache (0–100, Redis/Valkey only)

- **Redis/Valkey replication group**: `MultiAZ == enabled` → 100; otherwise 0.
- **Standalone Redis node (no replication group)**: 0, reason "single node, no replica".
- **Memcached / Serverless / other forms**: recorded as **N/A**, listed in the report with a note that they are excluded from scoring. Engine names are matched case-insensitively, so a `"Redis"` never silently falls into the N/A branch.

### 5.7 ELB (0–100, NLB only)

Scope is **NLB and ALB**; Classic and Gateway load balancers are out of scope and
recorded **N/A** in both dimensions.

- **Network Load Balancer**: enabled AZs ≥2 → 100; a single AZ → 0. Judged by configuration (`AvailabilityZones` from `DescribeLoadBalancers`), consistent with ASG.
- **Application Load Balancer**: recorded as **N/A** in the Multi-AZ dimension. AWS enforces at least two AZ subnets when an ALB is created, so there is no configuration lever to assess — scoring it would add a permanently-full-marks dimension that dilutes real failures elsewhere. Same principle as FSx Lustre in §5.5. (ALB *is* scored in the Cross-Region dimension, where having a standby is a real decision.)
- **Classic ELB and Gateway Load Balancer**: recorded as **N/A**, listed in the report with a note that scoring covers NLB and ALB only.

### 5.8 MSK (0–100, provisioned clusters only)

The same "who holds the coordination role" split as OpenSearch §5.4, with the same
answer as its dedicated-master arm: ZooKeeper / KRaft controllers are AWS-managed
(provisioned free with every cluster, no user lever), so only the **broker AZ
spread** is scored. MSK requires every client subnet to sit in a distinct AZ, so
the subnet count is the AZ count (`ZoneIds` wins when present):

- 3 AZs → **100**;
- 2 AZs → **50** — replicas of a replication-factor-3 topic split 2+1 across two
  AZs; losing the majority AZ leaves one in-sync replica, and the standard
  `min.insync.replicas=2` then blocks producers. MSK recommends three AZs and
  Express brokers require them;
- fewer → **0** (defensive; MSK does not normally allow single-subnet clusters).
- **MSK Serverless**: recorded **N/A** in both dimensions (AWS-managed, multi-AZ
  by design) — same treatment as ElastiCache Serverless in §5.6.
- **Known blind spot, stated in every reason**: topic `replication.factor` and
  `min.insync.replicas` live in the Kafka data plane (Admin API) and are invisible
  to the control plane — the same class of blind spot as OpenSearch index replica
  counts. A 3-AZ cluster whose topics have RF=1 still scores 100.

## 6. Cross-Region dimension criteria (0–100, independent of the Multi-AZ score)

- **Scope is decided by `pattern_id`, not by region count.** Only accounts whose pattern contains the marker `GS-001` (substring, case-insensitive) are expected to run a standby, so only they are scored here. Every other account is **N/A** — never 0 — and the note names the pattern and the missing marker, so a reader can tell "not required" from "required and missing".
- For an in-scope account the standby is **`regions[1]`**, and the loader guarantees there is no third region to consider (§2). `AccountSpec.standby_regions` still narrows to that one region on its own, so a spec constructed in code cannot widen the check by accident.
- An in-scope account that does not list exactly two regions never reaches the scan — the input loader rejects it (§2).
- Aggregation model identical to Multi-AZ (two-level, N/A dimensions excluded).

### Scored services and detection

| Service | Detection | Full score condition |
|---|---|---|
| RDS | Native API: cross-region read replica / Aurora Global Database | relation reaches `regions[1]` → 100 |
| EFS | Native API: replication configuration (`DescribeReplicationConfigurations` raises `ReplicationNotFound` — rather than returning an empty list — when the region has no replication configs at all; that is "no replications", never a scan failure) | destination is `regions[1]` → 100 |
| ElastiCache Redis | Native API: Global Datastore membership and member Regions | datastore has a member in `regions[1]` → 100 |
| ASG (non-EKS only) | **Name-matching heuristic**: same name after region-stripping exists in a standby region | match → 100 |
| EKS | **Name-matching heuristic**: same cluster name after region-stripping exists in a standby region (via `ListClusters`) | match → 100 |
| OpenSearch | **Name-matching heuristic**: same domain name after region-stripping exists in a standby region | match → 100 |
| ELB (NLB and ALB) | **Name-matching heuristic**: a load balancer of the **same type** and the same name after region-stripping exists in a standby region | match → 100 |
| MSK (provisioned) | **Name-matching heuristic**: same cluster name after region-stripping exists in a standby region (this estate does not use MSK Replicator; if it ever does, `ListReplicators` offers a native upgrade path) | match → 100 |
| FSx for Windows | **Name-matching heuristic**: same `Name` tag after region-stripping exists on a Windows file system in a standby region | match → 100 |

- **RDS distinguishes its two cluster families**: Aurora clusters use Global
  Database membership; non-Aurora RDS Multi-AZ DB clusters use
  `ReadReplicaIdentifiers`, just as standard DB instances do. A cluster or
  instance whose replication-source field is set is a replica and is not scored
  as another primary resource.
- **EKS is judged at the cluster level, directly through the EKS API**, and forms its own service dimension. Node-group ASGs are *excluded* from the ASG cross-region scoring (they remain scored in the Multi-AZ dimension) for three reasons: managed node-group ASG names are AWS-generated random strings (`eks-40bbb26b-…`) that can never match across regions; node-group names are commonly generic (`default`, `spot`, `system`) and would produce false positives against unrelated clusters in a standby region; and a cluster with four node groups would otherwise carry four times the weight of a single-node-group cluster. Cluster-level matching also covers Fargate-only clusters, which have no ASG at all. Exemption tags are read from the cluster's own tags.
- **ELB cross-region scoring covers NLB and ALB.** Unlike the Multi-AZ dimension, which scores NLB only (§5.7), a missing DR copy is a real gap for both types. Classic and Gateway load balancers are **N/A**, matching §5.7's scope.
- **The ELB match is on type *and* name.** An ALB in the primary region is only satisfied by an ALB in the standby: a same-named NLB is a different kind of entry point with different listeners and target semantics, and treating it as the standby would pass an account whose real DR copy does not exist.
- **FSx for Windows** has no native cross-region replication (AWS Backup copies are backups, not standby; DataSync sync is invisible from the FSx API), so it is scored with the name-matching heuristic over the **`Name` tag** (file system ids are random, so the tag is the only usable match value). A Windows file system **without a `Name` tag scores 0** with a reason explaining there is no name to match on — N/A would let untagged resources escape scoring. Other FSx types remain N/A.
- Native-relation detection obeys the same designated-standby rule as name
  matching: only a relation reaching **`regions[1]`** counts. A replica or Global
  Datastore member in another Region may be reported as diagnostic context, but
  it does not satisfy the GS-001 requirement and scores 0.
- Name-matching scans only the standby regions declared in the input.

### Name-matching rule (shared by ASG, EKS, OpenSearch, ELB, MSK, and FSx for Windows)

1. Pick the match value: ASG → the ASG name (EKS node-group ASGs, identified by the `eks:cluster-name` tag, are skipped entirely); EKS → the cluster name; OpenSearch → the domain name; ELB → the load balancer name; MSK → the cluster name; FSx for Windows → the `Name` tag. A type-scoped primary can match only the same scored type: in particular, a provisioned MSK cluster cannot match an MSK Serverless cluster.
2. **Strip region substrings**: delete substrings matching the AWS region pattern (regex `(?<![a-z0-9])[a-z]{2}(?:-[a-z]+)?-[a-z]+-\d(?![0-9])` — the optional middle segment covers 4-segment regions such as `us-gov-west-1`; the lookarounds keep token boundaries so `web-tier-2` is never mangled), then collapse leftover consecutive separators (`--`, `__`, etc.) into one. A name that is nothing but a region string falls back to itself rather than stripping to empty.
3. **Exact, case-insensitive match** after stripping; no fuzzy rules. Normalization
   case-folds both values before comparison. A name match counts as multi-region
   deployment; **node counts and configuration are not compared**.
4. The reason must state the heuristic nature, e.g. "name-matching heuristic: after region-stripping, matches ASG `myapp-nodes` in eu-west-1". For OpenSearch, if an ACTIVE cross-region connection is also detected (`DescribeOutboundConnections`), it is recorded in the reason as supporting evidence, but the verdict is based on the name match.

## 7. Exception (exemption) mechanism

- Each dimension has its own independent tag; they do not affect each other:
  - Multi-AZ: tag key = `skip-multiaz-assessment`
  - Cross-Region: tag key = `skip-cross-region-assessment`
  - The key names the **assessment**, not the feature. `disable-multiaz` would read
    as an instruction to turn a resource's redundancy off — the opposite of what
    the tag does, and of what this tool can do at all (§0 forbids any write). Even
    a bare `skip-multiaz` can be read as "this resource need not be multi-AZ";
    naming the assessment leaves one reading: do not evaluate this.
- **Key presence alone activates the exemption; the value is ignored.**
- Semantics are a floor, not a cap: final resource score = `max(actual score, 50)`. A resource that passes its check still gets 100.
- For the one split-scored service (EFS, two halves of 50): the exemption applies to the **resource total** (`max(sum of halves, 50)`), not per half.
- Reason wording: "multi-AZ not enabled, but exception tag `skip-multiaz-assessment` present; 50/100 per exemption rule".
- **No account-level exemption** (out of scope for v1; if needed, handle via an allowlist at the report layer, never inside the scoring engine).

## 8. N/A semantics and fault tolerance

Core principle: **"not scanned" and "scanned and found bad" are never conflated; N/A and 0 stay strictly separate.**

| Situation | Handling |
|---|---|
| Cannot assume the role in an account (role missing, trust policy refuses, master credentials expired, account suspended) | Account marked "inaccessible", both scores N/A, listed prominently in the report, scan continues |
| A service API call fails (e.g. missing `fsx:Describe*` permission) | That service dimension marked "scan failed", N/A; other services scored normally; reason records the error |
| Account has none of a resource type | Service dimension N/A, excluded from the mean |
| Resource type out of scoring scope (Lustre, Memcached, ...) | Listed per resource in the report with explanation, excluded from scoring |
| Account outside the Cross-Region pattern (§6) | Cross-Region score N/A, with a note naming the pattern and the missing marker so "not required" stays distinguishable from "required and missing" |
| Payload contradicts itself (non-array `accounts`, non-mapping entry, bad account id, non-string pattern, empty/duplicate regions, a marked pattern without exactly two distinct regions) | Rejected at parse time by `InputError` — nothing is scanned |

Failure isolation follows the same dimensional boundary as scoring. Primary
resource discovery is performed first; once Multi-AZ results exist, a standby
Region or Cross-Region-only API failure marks **only the Cross-Region service
dimension** N/A and preserves the Multi-AZ result. Accounts outside Cross-Region
scope do not call Cross-Region-only APIs such as RDS Global Clusters, EFS
replication configurations, or OpenSearch outbound connections.

## 9. Output

- **JSON (source of truth)**: org summary → per account (both scores, inaccessible flag, pass-through `pattern_id` / `application`) → per service dimension score → per resource (score, reason, region, exemption flag). Account metadata is emitted once at account level rather than duplicated into every resource.
- **HTML (human report)**: single self-contained file (no external resources, no CDN, autoescape unconditionally on — `select_autoescape` would miss the `.j2` suffix and reasons/tags/names are externally influenced). Structure: header → summary tiles → org summary table (both scores per account) → per-account collapsible detail (service dimension scores → resource scores and reasons → notes). Shows `pattern_id` / application details, inaccessible accounts, and the out-of-scope resource list.
- **Account lookup** is built in: a filter box over account id / pattern / environment / application / region that narrows both the summary table and the detail panels (`/` focuses it, Escape clears), clickable summary rows and `#acct-<id>` deep links that open the matching detail, and a per-row jump link — finding one account among hundreds must not require scrolling.
- **Presentation rules**: brand colour dresses the chrome only; score cells use the reserved status palette as a border beside a visible number (score bands: ≥15 good, ≥10 partial, else bad; `None` renders as N/A, never as 0), so colour never carries meaning alone.
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

- **Python 3.11+ / boto3**, imported as a library (`from assessment.resilience import score`).
- Required AWS permissions: read-only `Describe*/List*` (covered by `ReadOnlyAccess` or `SecurityAudit`).
- HTML rendered with a template engine (Jinja2); JSON is the primary artifact, HTML is rendered from the same data.
- Testing: pytest; the AWS API layer is wrapped behind injectable interfaces; the scoring engine is pure functions, unit-testable offline.

## 12. Explicitly out of scope for v1 (YAGNI list)

- Account-level exemptions (allowlist)
- ALB / Classic ELB / Gateway LB multi-AZ scoring (only NLB is scored in the Multi-AZ dimension)
- EKS multi-AZ scoring as its own dimension (node-group AZ coverage is already scored under ASG; EKS control-plane AZ spread is AWS-managed and has no configuration lever)
- OpenSearch data-plane checks (index replica counts), reading cross-cluster replication rules
- Network topology checks beyond EFS mount targets
- Trend tracking / historical comparison / persistence
- Auto-enumerating Organizations accounts (the list is supplied externally)
- Multi-AZ scanning outside the primary region
