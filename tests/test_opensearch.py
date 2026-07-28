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
