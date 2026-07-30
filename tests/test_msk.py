# tests/test_msk.py
from hascore.scanners.msk import evaluate_msk_crossregion, evaluate_msk_multiaz

R = "ap-south-1"


def cluster(name, subnets=3, ctype="PROVISIONED", tags=None, zone_ids=None):
    return {
        "name": name,
        "arn": f"arn:aws:kafka:{R}:111111111111:cluster/{name}/uuid-1",
        "type": ctype,
        "subnets": [f"subnet-{i}" for i in range(subnets)],
        "zone_ids": zone_ids if zone_ids is not None else [],
        "tags": tags or {},
    }


def by_id(scores):
    return {s.resource_id: s for s in scores}


# --- multi-AZ: 3 AZ -> 20, 2 AZ -> 10, serverless N/A ---

def test_three_az_brokers_score_20():
    s = by_id(evaluate_msk_multiaz([cluster("k3", subnets=3)], R))["k3"]
    assert s.score == 100.0
    assert "3 AZ" in s.reason


def test_two_az_brokers_score_10_for_min_isr_risk():
    """With RF=3 in two AZs the replicas split 2+1; losing the majority AZ
    leaves one in-sync replica, and min.insync.replicas=2 blocks producers."""
    s = by_id(evaluate_msk_multiaz([cluster("k2", subnets=2)], R))["k2"]
    assert s.score == 50.0
    assert "min.insync.replicas" in s.reason


def test_zone_ids_win_over_subnet_count():
    """ZoneIds is the direct signal when present; subnets are the fallback
    (MSK enforces distinct AZs per subnet, so the count is trustworthy)."""
    s = by_id(evaluate_msk_multiaz(
        [cluster("kz", subnets=2, zone_ids=["aps1-az1", "aps1-az2", "aps1-az3"])], R))["kz"]
    assert s.score == 100.0


def test_serverless_is_na_with_note():
    s = by_id(evaluate_msk_multiaz([cluster("sl", ctype="SERVERLESS")], R))["sl"]
    assert s.score is None
    assert "Serverless" in s.reason


def test_exemption_floors_a_two_az_cluster_no_lower():
    """The tag floors failing scores at 10; a 2-AZ cluster already sits at 10."""
    tagged = cluster("k2t", subnets=2, tags={"disable-multiaz": ""})
    s = by_id(evaluate_msk_multiaz([tagged], R))["k2t"]
    assert s.score == 50.0 and s.exempted is False


def test_topic_blind_spot_is_stated_in_the_reason():
    """Topic replication.factor / min.insync.replicas live in the Kafka data
    plane; the reason must say the score does not cover them."""
    s = by_id(evaluate_msk_multiaz([cluster("k3", subnets=3)], R))["k3"]
    assert "data plane" in s.reason


# --- cross-region: name-matching heuristic (this estate does not use MSK Replicator) ---

def test_cluster_name_match_scores_20():
    c = cluster("payments-ap-south-1-kafka")
    s = by_id(evaluate_msk_crossregion([c], {"ap-south-2": {"payments-kafka"}}, R))[c["name"]]
    assert s.score == 100.0
    assert "heuristic" in s.reason
    assert "ap-south-2" in s.reason


def test_no_matching_cluster_scores_0():
    c = cluster("payments-kafka")
    s = by_id(evaluate_msk_crossregion([c], {"ap-south-2": {"billing-kafka"}}, R))["payments-kafka"]
    assert s.score == 0.0
    assert "no MSK cluster matching" in s.reason


def test_no_match_with_exemption_floors_to_10():
    tagged = cluster("kt", tags={"disable-crossregion": "yes"})
    s = by_id(evaluate_msk_crossregion([tagged], {"ap-south-2": set()}, R))["kt"]
    assert s.score == 50.0 and s.exempted


def test_serverless_is_na_in_cross_region_too():
    s = by_id(evaluate_msk_crossregion(
        [cluster("sl", ctype="SERVERLESS")], {"ap-south-2": set()}, R))["sl"]
    assert s.score is None
