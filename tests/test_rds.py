# tests/test_rds.py
from assessment.resilience.scanners.rds import evaluate_rds_crossregion, evaluate_rds_multiaz

R = "us-east-1"


def inst(iid, az="us-east-1a", multi_az=False, engine="mysql", replicas=(), source=None, tags=(),
         cluster_id=None):
    instance = {
        "DBInstanceIdentifier": iid,
        "AvailabilityZone": az,
        "MultiAZ": multi_az,
        "Engine": engine,
        "ReadReplicaDBInstanceIdentifiers": list(replicas),
        "ReadReplicaSourceDBInstanceIdentifier": source,
        "TagList": [{"Key": k, "Value": v} for k, v in tags],
    }
    if cluster_id:
        instance["DBClusterIdentifier"] = cluster_id
    return instance


def by_id(scores):
    return {s.resource_id: s for s in scores}


# --- multi-AZ ---

def test_multiaz_enabled_scores_20():
    scores = by_id(evaluate_rds_multiaz([inst("db1", multi_az=True)], [], R))
    assert scores["db1"].score == 100.0
    assert "MultiAZ is enabled" in scores["db1"].reason


def test_cross_az_replica_scores_20_and_replica_not_scored():
    instances = [
        inst("primary", az="us-east-1a", replicas=["replica"]),
        inst("replica", az="us-east-1b", source="primary"),
    ]
    scores = by_id(evaluate_rds_multiaz(instances, [], R))
    assert set(scores) == {"primary"}
    assert scores["primary"].score == 100.0
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
    instances = [inst("db1"), inst("db2", tags=[("skip-multiaz-assessment", "")])]
    scores = by_id(evaluate_rds_multiaz(instances, [], R))
    assert scores["db1"].score == 0.0
    assert scores["db2"].score == 50.0 and scores["db2"].exempted


def test_aurora_scored_at_cluster_level():
    instances = [
        inst("a1", az="us-east-1a", engine="aurora-mysql"),
        inst("a2", az="us-east-1b", engine="aurora-mysql"),
        inst("solo", az="us-east-1a", engine="aurora-postgresql"),
    ]
    clusters = [
        {"DBClusterIdentifier": "c-multi", "DBClusterArn": "arn:aws:rds:us-east-1:1:cluster:c-multi",
         "Engine": "aurora-mysql",
         "DBClusterMembers": [{"DBInstanceIdentifier": "a1"}, {"DBInstanceIdentifier": "a2"}], "TagList": []},
        {"DBClusterIdentifier": "c-solo", "DBClusterArn": "arn:aws:rds:us-east-1:1:cluster:c-solo",
         "Engine": "aurora-postgresql",
         "DBClusterMembers": [{"DBInstanceIdentifier": "solo"}], "TagList": []},
    ]
    scores = by_id(evaluate_rds_multiaz(instances, clusters, R))
    assert set(scores) == {"c-multi", "c-solo"}
    assert scores["c-multi"].score == 100.0
    assert scores["c-solo"].score == 0.0


def test_rds_multiaz_db_cluster_is_scored_once_at_cluster_level():
    instances = [
        inst("writer", az="us-east-1a", cluster_id="db-cluster"),
        inst("reader-1", az="us-east-1b", cluster_id="db-cluster"),
        inst("reader-2", az="us-east-1c", cluster_id="db-cluster"),
    ]
    clusters = [{
        "DBClusterIdentifier": "db-cluster",
        "Engine": "mysql",
        "MultiAZ": True,
        "DBClusterMembers": [
            {"DBInstanceIdentifier": "writer"},
            {"DBInstanceIdentifier": "reader-1"},
            {"DBInstanceIdentifier": "reader-2"},
        ],
        "TagList": [],
    }]
    scores = evaluate_rds_multiaz(instances, clusters, R)
    assert [(score.resource_id, score.score) for score in scores] == [("db-cluster", 100.0)]
    assert "RDS Multi-AZ DB cluster" in scores[0].reason


def test_rds_cluster_read_replica_is_not_scored_separately():
    cluster = {
        "DBClusterIdentifier": "db-cluster-replica",
        "Engine": "mysql",
        "MultiAZ": True,
        "ReplicationSourceIdentifier": "arn:aws:rds:eu-west-1:1:cluster:db-cluster",
        "DBClusterMembers": [],
        "TagList": [],
    }
    assert evaluate_rds_multiaz([], [cluster], R) == []


# --- cross-region ---

def test_cross_region_replica_scores_20():
    arn = "arn:aws:rds:eu-west-1:111111111111:db:dr-replica"
    scores = by_id(evaluate_rds_crossregion(
        [inst("db1", replicas=[arn])], [], [], R, ["us-east-1", "eu-west-1"]))
    assert scores["db1"].score == 100.0
    assert "eu-west-1" in scores["db1"].reason


def test_cross_region_replica_outside_the_designated_standby_scores_0():
    arn = "arn:aws:rds:ap-south-1:111111111111:db:dr-replica"
    scores = by_id(evaluate_rds_crossregion(
        [inst("db1", replicas=[arn])], [], [], R, ["us-east-1", "eu-west-1"]))
    assert scores["db1"].score == 0.0
    assert "ap-south-1" in scores["db1"].reason
    assert "eu-west-1" in scores["db1"].reason


def test_no_cross_region_replica_scores_0_and_exemption_applies():
    scores = by_id(evaluate_rds_crossregion(
        [inst("db1", tags=[("skip-cross-region-assessment", "")])], [], [], R, ["us-east-1", "eu-west-1"]))
    assert scores["db1"].score == 50.0 and scores["db1"].exempted


def test_aurora_cluster_with_no_resolvable_members_reason_is_truthful():
    cluster = {"DBClusterIdentifier": "c-empty", "DBClusterArn": "arn:aws:rds:us-east-1:1:cluster:c-empty",
               "Engine": "aurora-mysql", "DBClusterMembers": [], "TagList": []}
    scores = by_id(evaluate_rds_multiaz([], [cluster], R))
    assert scores["c-empty"].score == 0.0
    assert "only one AZ" not in scores["c-empty"].reason
    assert "no cluster member" in scores["c-empty"].reason


def test_aurora_global_database_member_scores_20():
    cluster = {"DBClusterIdentifier": "c1", "DBClusterArn": "arn:aws:rds:us-east-1:1:cluster:c1",
               "Engine": "aurora-mysql", "DBClusterMembers": [], "TagList": []}
    global_clusters = [{"GlobalClusterMembers": [
        {"DBClusterArn": "arn:aws:rds:us-east-1:1:cluster:c1"},
        {"DBClusterArn": "arn:aws:rds:eu-west-1:1:cluster:c1-dr"},
    ]}]
    scores = by_id(evaluate_rds_crossregion([], [cluster], global_clusters, R, ["us-east-1", "eu-west-1"]))
    assert scores["c1"].score == 100.0
    assert "eu-west-1" in scores["c1"].reason


def test_aurora_global_database_without_the_designated_standby_scores_0():
    cluster = {
        "DBClusterIdentifier": "c1",
        "DBClusterArn": "arn:aws:rds:us-east-1:1:cluster:c1",
        "Engine": "aurora-mysql",
        "DBClusterMembers": [],
        "TagList": [],
    }
    global_clusters = [{"GlobalClusterMembers": [
        {"DBClusterArn": "arn:aws:rds:us-east-1:1:cluster:c1"},
        {"DBClusterArn": "arn:aws:rds:ap-south-1:1:cluster:c1-dr"},
    ]}]
    scores = by_id(evaluate_rds_crossregion(
        [], [cluster], global_clusters, R, ["us-east-1", "eu-west-1"]))
    assert scores["c1"].score == 0.0
    assert "ap-south-1" in scores["c1"].reason
    assert "eu-west-1" in scores["c1"].reason


def test_rds_multiaz_db_cluster_cross_region_replica_scores_20():
    cluster = {
        "DBClusterIdentifier": "db-cluster",
        "DBClusterArn": "arn:aws:rds:us-east-1:1:cluster:db-cluster",
        "Engine": "mysql",
        "ReadReplicaIdentifiers": ["arn:aws:rds:eu-west-1:1:cluster:db-cluster-dr"],
        "TagList": [],
    }
    scores = by_id(evaluate_rds_crossregion(
        [], [cluster], [], R, ["us-east-1", "eu-west-1"]))
    assert scores["db-cluster"].score == 100.0
    assert "eu-west-1" in scores["db-cluster"].reason
    assert "Aurora" not in scores["db-cluster"].reason


def test_rds_multiaz_db_cluster_replica_outside_designated_standby_scores_0():
    cluster = {
        "DBClusterIdentifier": "db-cluster",
        "DBClusterArn": "arn:aws:rds:us-east-1:1:cluster:db-cluster",
        "Engine": "mysql",
        "ReadReplicaIdentifiers": ["arn:aws:rds:ap-south-1:1:cluster:db-cluster-dr"],
        "TagList": [],
    }
    scores = by_id(evaluate_rds_crossregion(
        [], [cluster], [], R, ["us-east-1", "eu-west-1"]))
    assert scores["db-cluster"].score == 0.0
    assert "ap-south-1" in scores["db-cluster"].reason
    assert "eu-west-1" in scores["db-cluster"].reason


def test_replica_only_in_another_region_is_not_reported_as_no_replicas():
    """A cross-region replica does not protect against an AZ failure in the
    primary region, so 0 is the right score — but the reason must not claim the
    instance has no replicas, because it does."""
    arn = "arn:aws:rds:eu-west-1:111111111111:db:dr-replica"
    scores = by_id(evaluate_rds_multiaz([inst("db1", replicas=[arn])], [], R))
    assert scores["db1"].score == 0.0
    assert "no read replicas" not in scores["db1"].reason
    assert "outside" in scores["db1"].reason
    assert "cross-region" in scores["db1"].reason


def test_crossregion_without_a_standby_region_is_a_contract_violation():
    """scan() only calls this for in-scope accounts, which the loader guarantees
    have two regions; reaching it otherwise is a bug, not a silent no-op."""
    import pytest
    with pytest.raises(ValueError, match="standby"):
        evaluate_rds_crossregion([inst("db1")], [], [], R, [R])
