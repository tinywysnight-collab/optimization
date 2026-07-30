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


def sc(name, engine="redis"):
    return {"ServerlessCacheName": name, "ARN": f"arn:aws:elasticache:us-east-1:1:serverlesscache:{name}",
            "Engine": engine}


def by_id(scores):
    return {s.resource_id: s for s in scores}


def global_group(global_id, *regions):
    return {
        "GlobalReplicationGroupId": global_id,
        "Members": [{"ReplicationGroupRegion": region} for region in regions],
    }


def test_replication_group_multi_az_enabled_scores_20():
    scores = by_id(evaluate_elasticache_multiaz([rg("rg-1")], [], [], {}, R))
    assert scores["rg-1"].score == 100.0


def test_replication_group_multi_az_disabled_scores_0_exemption_floors():
    tags = {"arn:aws:elasticache:us-east-1:1:replicationgroup:rg-2": {"skip-multiaz": ""}}
    scores = by_id(evaluate_elasticache_multiaz(
        [rg("rg-1", multi_az="disabled"), rg("rg-2", multi_az="disabled")], [], [], tags, R))
    assert scores["rg-1"].score == 0.0
    assert scores["rg-2"].score == 50.0 and scores["rg-2"].exempted


def test_standalone_redis_scores_0_member_clusters_skipped():
    scores = by_id(evaluate_elasticache_multiaz(
        [rg("rg-1")], [cc("solo"), cc("member", rg_id="rg-1")], [], {}, R))
    assert set(scores) == {"rg-1", "solo"}
    assert scores["solo"].score == 0.0
    assert "single node" in scores["solo"].reason.lower()


def test_engine_match_is_case_insensitive():
    scores = by_id(evaluate_elasticache_multiaz([], [cc("solo", engine="Redis")], [], {}, R))
    assert scores["solo"].score == 0.0  # scored, not silently N/A


def test_memcached_is_na_with_note():
    scores = by_id(evaluate_elasticache_multiaz([], [cc("mc", engine="memcached")], [], {}, R))
    assert scores["mc"].score is None
    assert "memcached" in scores["mc"].reason.lower()


def test_serverless_cache_is_na_and_listed():
    scores = by_id(evaluate_elasticache_multiaz([], [], [sc("sl-1")], {}, R))
    assert scores["sl-1"].score is None
    assert "Serverless" in scores["sl-1"].reason


def test_global_datastore_member_scores_20_cross_region():
    scores = by_id(evaluate_elasticache_crossregion(
        [rg("rg-1", global_id="gd-xyz")], [], [], {},
        [global_group("gd-xyz", R, "eu-west-1")], R, "eu-west-1"))
    assert scores["rg-1"].score == 100.0
    assert "gd-xyz" in scores["rg-1"].reason


def test_global_datastore_without_the_designated_standby_scores_0():
    global_groups = [{
        "GlobalReplicationGroupId": "gd-xyz",
        "Members": [
            {"ReplicationGroupRegion": "us-east-1"},
            {"ReplicationGroupRegion": "ap-south-1"},
        ],
    }]
    scores = by_id(evaluate_elasticache_crossregion(
        [rg("rg-1", global_id="gd-xyz")], [], [], {}, global_groups, R, "eu-west-1"))
    assert scores["rg-1"].score == 0.0
    assert "ap-south-1" in scores["rg-1"].reason
    assert "eu-west-1" in scores["rg-1"].reason


def test_no_global_datastore_scores_0_cross_region():
    scores = by_id(evaluate_elasticache_crossregion(
        [rg("rg-1")], [cc("solo")], [], {}, [], R, "eu-west-1"))
    assert scores["rg-1"].score == 0.0
    assert scores["solo"].score == 0.0


def test_serverless_cache_is_na_cross_region():
    scores = by_id(evaluate_elasticache_crossregion(
        [], [], [sc("sl-1")], {}, [], R, "eu-west-1"))
    assert scores["sl-1"].score is None


def test_memcached_is_na_and_listed_cross_region():
    scores = by_id(evaluate_elasticache_crossregion(
        [], [cc("mc", engine="memcached")], [], {}, [], R, "eu-west-1"))
    assert scores["mc"].score is None
    assert "memcached" in scores["mc"].reason.lower()
