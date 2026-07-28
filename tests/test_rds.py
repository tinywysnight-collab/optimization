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


def test_aurora_cluster_with_no_resolvable_members_reason_is_truthful():
    cluster = {"DBClusterIdentifier": "c-empty", "DBClusterArn": "arn:aws:rds:us-east-1:1:cluster:c-empty",
               "DBClusterMembers": [], "TagList": []}
    scores = by_id(evaluate_rds_multiaz([], [cluster], R))
    assert scores["c-empty"].score == 0.0
    assert "only one AZ" not in scores["c-empty"].reason
    assert "no cluster member" in scores["c-empty"].reason


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
