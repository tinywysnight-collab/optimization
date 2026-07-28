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


def test_engine_match_is_case_insensitive():
    scores = by_id(evaluate_elasticache_multiaz([], [cc("solo", engine="Redis")], {}, R))
    assert scores["solo"].score == 0.0  # scored, not silently N/A


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
