# Scoring logic, resource by resource

What each service is checked for, which API field decides it, and why the
threshold sits where it does. This is a reference for reading a report: when a
score surprises you, find the service here and the rule that produced it.

The authoritative statement of these rules is
[the design spec](superpowers/specs/2026-07-28-multiaz-compliance-scoring-design.md)
(§5 Multi-AZ, §6 Cross-Region); this document restates them per resource with the
field names. Behaviour changes land in the spec first — see the spec-first
workflow in `AGENTS.md`.

## How the numbers combine

Every resource scores **0–100**, or **N/A**. The two are never mixed up:

| | Meaning | Effect on the account score |
|---|---|---|
| **0** | Checked, and it fails | Drags the average down |
| **N/A** | Out of scope, or not checked | Excluded entirely — never counted as a zero |

Two levels of averaging, per dimension:

1. **Resource → service.** Arithmetic mean of that service's scored resources.
   All-N/A means the service is N/A.
2. **Service → account.** Equal-weight mean of the services that have a score.
   A service with no resources never appears, so an account is not penalised for
   not using a service.

Both are rounded to one decimal.

**The account score is the only number that ranks an account.** When a dimension
covers more than one region — a `PTM` account — the report also breaks each
service down per region, so a pooled score like `rds 66.7` can be traced to the
region that dragged it down. That breakdown is display only and never feeds back
into the account number; it is omitted for single-region accounts, where it would
just restate the service scores.

The unit is deliberately the same one the final organization-wide score is
reported in, so rolling several dimensions together is a weighted mean with no
rescaling. Granularity stays coarse — the judgements underneath are pass /
partial / fail, so scores land on **100 / 50 / 0** rather than implying a
precision the checks do not have. Exempted resources are the one exception, landing
on 70 (below).

**Exemption tags** put a floor under a failing score, never a cap on a passing
one: `max(score, 70)`. Tag key presence is enough — the value is ignored, and
matching is case-insensitive.

70 sits deliberately between the two: an exemption is a gap someone reviewed and
accepted, so it should not score near a resource nobody looked at, but the tag is
self-applied and unvalidated, so a floor near 100 would make tagging cheaper than
being redundant. The 30-point gap keeps passing the check worthwhile.

- `skip-multiaz-assessment` — the Multi-AZ dimension
- `skip-cross-region-assessment` — the Cross-Region dimension

For services scored as two halves (EFS), the tag applies to the **resource
total**, not to each half.

## Which accounts get a Cross-Region score

Scope is decided by `pattern_id`, not by how many regions the payload lists. Only
an account whose pattern contains **`GS-001`** (substring, case-insensitive) is
expected to run a standby, and it must list **exactly two regions** — primary,
then standby — or the payload is rejected before any scan starts.

Every other account records **N/A** for Cross-Region, with a note naming the
pattern. "Not required" stays distinguishable from "required and missing".

`regions[1]` is *the* standby. A replica or copy sitting in some third region
does not satisfy the check, and the reason says where it actually is.

---

# Multi-AZ dimension

**Which regions are scanned depends on the pattern.** An account whose
`pattern_id` contains **`PTM`** runs mutually independent deployments, so every
listed region is assessed; every other account is scanned in `regions[0]` alone,
since a `GS-001` standby is a copy rather than a separate estate.

Resources from all scanned regions pool into **one** service dimension — an
account gets one `rds` score, not one per region — and each resource row carries
its own region, so a failing region is still identifiable.

## RDS  — 0–100

Two kinds of scoring unit. **Read replicas and DB cluster members are never
scored on their own** (`DBClusterIdentifier` set, or
`ReadReplicaSourceDBInstanceIdentifier` set) — scoring a replica would give 0 to
the very redundancy the primary was credited for.

### Standalone instances

| Condition | Score |
|---|---|
| `MultiAZ == true` | **100** |
| A read replica in a **different AZ** of this region | **100** |
| Replicas exist but all share the primary's AZ | **0** |
| Replicas exist only in other regions | **0** |
| No replicas | **0** |

A same-AZ replica dies with the AZ, so it is not redundancy. A cross-region
replica is a DR asset, not an AZ one — the reason says the replica exists and
where, never "no read replicas", which would send someone to build what they
already have. It is credited in the Cross-Region dimension instead.

### Clusters (Aurora, and RDS Multi-AZ DB clusters)

Aurora's `MultiAZ` field is always false, so the two cluster kinds are read
differently:

- **Aurora** — redundancy is whether member instances span ≥2 AZs.
- **RDS Multi-AZ DB cluster** (MySQL/PostgreSQL) — `MultiAZ` must be true **and**
  members must span ≥2 AZs.

Either way: ≥2 member AZs → **100**, otherwise **0**. A cluster whose members'
AZs cannot be resolved says so rather than claiming one AZ.

A cluster with `ReplicationSourceIdentifier` is itself a replication target and
is skipped, for the same reason replicas are.

## EFS — two halves of 50

| Half | Field | Full marks |
|---|---|---|
| Storage redundancy | `AvailabilityZoneId` absent → Regional | **50** |
| Mount target coverage | mount targets in ≥2 AZs | **50** |

Regional storage replicates across AZs; One Zone does not. But data surviving is
not the same as data being reachable: with mount targets in one AZ only, clients
in a failed AZ cannot mount at all. Hence two independent halves.

A One Zone file system can only have one mount target, so it scores 0+0 without
special-casing.

Mount target AZs are read from `AvailabilityZoneId`, falling back to
`AvailabilityZoneName` only when no entry has an ID — mixing the two forms would
count one physical AZ twice and manufacture a passing score.

## ASG  — 0–100

Configured AZs (`AvailabilityZones`) ≥2 → **100**, otherwise **0**.

Judged by **configuration, not running instances**. A group with desired capacity
1 spanning three AZs still recovers into another AZ; scoring live instances would
make the number swing with autoscaling and would punish a correctly configured
group for being idle.

EKS node group ASGs are **scored normally here** — their AZ coverage is a real
per-node-group decision — with the owning cluster named in the reason. (The
Cross-Region dimension treats EKS differently; see below.)

## OpenSearch  — 0–100

The rule forks on **who holds the master role**.

### With dedicated masters (`DedicatedMasterEnabled`)

Master placement is AWS-managed — AWS spreads dedicated masters across three AZs
on its own, even for a two-AZ domain — so the only operator decision left is the
data-node spread, and it is binary:

| Data nodes | Score |
|---|---|
| ≥2 AZs (zone awareness on) | **100** |
| Single AZ | **0** |

A healthy control plane over non-redundant data is not high availability: quorum
would be protecting a cluster that loses its data with its one AZ.

Master **count** is advisory only. Fewer than three, or an even number, adds a
note — OpenSearch 7.x+ keeps an odd voting set, so 2 acts as 1 — but does not
change the score. The console now offers only 3 or 5; this is for legacy domains.

### Without dedicated masters

Data nodes hold the master role, so their own spread decides whether quorum
survives:

| Data nodes | Score | Why |
|---|---|---|
| 3 AZs | **100** | A majority survives any single-AZ loss |
| 2 AZs | **50** | A partition risks split-brain, and losing the larger AZ loses quorum |
| 1 AZ | **0** | No redundancy |

`ZoneAwarenessEnabled` with no `ZoneAwarenessConfig` is the old-style form and
counts as **2 AZs**, not one.

## FSx  — 0–100, Windows only

`WindowsConfiguration.DeploymentType` contains `MULTI_AZ` → **100**, else **0**.

**Lustre, ONTAP and OpenZFS record N/A** and are listed with their type. Lustre
in particular has no multi-AZ deployment at all, so a 0 would be a permanent
score against something with no remedy.

## ElastiCache  — 0–100, Redis/Valkey only

| Resource | Score |
|---|---|
| Replication group with `MultiAZ == "enabled"` | **100** |
| Replication group otherwise | **0** |
| Standalone Redis/Valkey node (no replication group) | **0** |
| Memcached, and other engines | **N/A** |
| Serverless caches | **N/A** |

What the console calls a "Redis cluster" is a **replication group** in the API;
`CacheCluster` is a single node inside one. Nodes carrying a `ReplicationGroupId`
are skipped so a six-node cluster is one score, not six zeros.

`MultiAZ == "enabled"` already implies replicas in other AZs — AWS only enables
it when every shard has a replica outside the primary's AZ — so no separate AZ
check is needed. Memcached has no replication at all, so multi-AZ redundancy is
not a concept that applies; Serverless has no user-facing HA setting.

Engine matching is case-insensitive.

## ELB  — 0–100, NLB only

Scope for both dimensions is **NLB and ALB**. Classic and Gateway load balancers
record **N/A** everywhere.

Of those two, only the NLB is scored here: enabled AZs ≥2 → **100**, else **0**.

**ALB records N/A in this dimension.** AWS requires at least two AZ subnets at
creation, so there is no configuration lever to assess and every ALB would score
full marks, diluting the real failures elsewhere. It *is* scored in the
Cross-Region dimension, where having a standby is a genuine decision.

## MSK  — 0–100, provisioned only

Broker AZ spread, from `ZoneIds` when present, otherwise the count of
`ClientSubnets` (MSK requires every subnet in a distinct AZ, so the count is the
AZ count):

| Brokers | Score |
|---|---|
| 3 AZs | **100** |
| 2 AZs | **50** |
| fewer | **0** |

Two AZs is a real write-availability risk, not just a preference: replicas of a
replication-factor-3 topic split 2+1, so losing the majority AZ leaves one
in-sync replica and the standard `min.insync.replicas=2` blocks producers. AWS
recommends three AZs, and Express brokers require them.

ZooKeeper/KRaft controllers are AWS-managed and free, so — like OpenSearch
dedicated masters — they are not scored. **MSK Serverless records N/A.**

Every MSK reason states that topic-level `replication.factor` and
`min.insync.replicas` are **not** covered; see the blind spots below.

## EKS

Not scored in this dimension. Node group AZ coverage is already scored under ASG,
and the EKS control plane's AZ spread is AWS-managed with no configuration lever.

---

# Cross-Region dimension

Only for `GS-001` accounts. The standby is `regions[1]`.

Two kinds of detection, and the difference matters when reading a report:

## Native — the API states the relationship

Deterministic. The relationship exists in AWS's own data model.

| Service | Signal | Full marks |
|---|---|---|
| **RDS** (instance) | cross-region read replica ARN | replica in the **designated standby** |
| **RDS** (Aurora cluster) | Aurora Global Database membership | a member cluster in the **designated standby** |
| **RDS** (Multi-AZ DB cluster) | `ReadReplicaIdentifiers` ARNs | replica in the **designated standby** |
| **EFS** | replication configuration destination | destination is the **designated standby** |
| **ElastiCache** | Global Datastore membership | a member in the **designated standby** |

A copy in some *other* region scores **0**, and the reason names where it
actually is — the pattern promises a specific standby, and a replica elsewhere
does not satisfy it.

## Heuristic — matching by name

For services where AWS models no cross-region relationship at all. The region
token is stripped from both names and what remains must match exactly.

| Service | Match value |
|---|---|
| **ASG** (non-EKS only) | ASG name |
| **EKS** | cluster name (from `ListClusters`) |
| **OpenSearch** | domain name |
| **ELB** (NLB and ALB) | load balancer **type paired with** its name |
| **MSK** | cluster name |
| **FSx for Windows** | `Name` tag |

So `payments-ap-south-1-web` matches `payments-ap-south-2-web`: both strip to
`payments-web`.

**Every reason says it is a heuristic**, because it can be wrong in both
directions — two unrelated resources that happen to share a name will match, and
a real DR pair named inconsistently will not.

**ELB matches on type as well as name.** An ALB is only satisfied by an ALB: an
NLB of the same name is a different kind of entry point, with different listeners
and target semantics, and crediting it would pass an account whose real DR copy
does not exist. Names collide across types often, since an ALB and an NLB
fronting the same service tend to be named alike.

Region stripping handles 3- and 4-segment regions (`ap-south-1`,
`us-gov-west-1`), is case-insensitive, and requires token boundaries so
`web-tier-2` is left alone.

**EKS node group ASGs are excluded from ASG matching** and scored once at the
cluster level: managed node group ASG names carry AWS-generated random suffixes
that can never match across regions, and node group names like `default` or
`spot` would match unrelated clusters. Cluster-level matching also covers
Fargate-only clusters, which have no ASG at all.

For OpenSearch, an ACTIVE cross-region connection (`DescribeOutboundConnections`)
is added to the reason as supporting evidence, but the verdict stays with the
name match — that connection serves cross-cluster *search* as well as
replication, so its presence does not prove data is being copied.

**MSK Replicator is not used** for detection, because this estate does not run
it. If that changes, `ListReplicators` is a native upgrade path.

## Not scored

**FSx for Windows** has no native cross-region replication — AWS Backup copies
are backups, not a standby, and DataSync runs above the API — so only the
`Name`-tag heuristic applies.

---

# Blind spots

Three things this tool cannot see. Each can make a resource score 100 while it
would still lose data or availability.

**OpenSearch index replica counts.** A domain spanning three AZs whose indexes
have `number_of_replicas: 0` loses data when an AZ fails. Replica count is a
data-plane setting, per index and changeable at any time, so a control-plane scan
cannot see it — and it drifts: a bulk load that sets replicas to 0 and never
restores them leaves no trace in any AWS API.

**MSK topic replication.** Same shape: `replication.factor` and
`min.insync.replicas` are Kafka Admin API settings. A 3-AZ cluster whose topics
have RF=1 scores 100 and still loses data with one broker.

**OpenSearch masters on legacy instance types.** The automatic three-AZ master
placement also needs the chosen instance type to exist in three AZs. An
older-generation type that does not forces masters into two zones — a documented
"50/50 chance of downtime" — but **only for a domain that selected two AZs**: a
three-AZ domain on such a type fails at creation, so that pairing cannot exist.
The input is visible (`ClusterConfig.DedicatedMasterType`); what is missing is a
maintained list of legacy types, which would go stale as fast as it was written.

This entry previously also covered two-AZ regions such as `us-west-1`, where the
three-AZ placement has nowhere to go. That is now a **stated assumption** rather
than a blind spot: every region this estate scans has at least three AZs. Adding a
two-AZ region would reinstate the gap and need an EC2 `DescribeAvailabilityZones`
call to close it.

None of these is a defect to fix in code; detecting them needs a network path to
the data plane, or an extra EC2 call plus a legacy instance-type list. They are
recorded here, and in the reasons, so no one reads a 100 as more assurance than it
carries.
