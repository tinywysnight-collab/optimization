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


def test_dedicated_masters_with_cross_az_data_nodes_score_20():
    """Rule 1: with dedicated masters, only the data-node AZ spread matters —
    master placement is AWS-managed. 2 AZs and 3 AZs both pass."""
    for az in (2, 3):
        d = domain(f"d{az}", za=True, az_count=az, dedicated=True, master_count=3)
        scores = by_id(evaluate_opensearch_multiaz([d], {}, R))
        assert scores[f"d{az}"].score == 100.0, f"{az} AZs should be full marks"


def test_dedicated_masters_with_single_az_data_nodes_score_0():
    """Healthy control plane over non-redundant data is still not HA: quorum
    would be protecting a cluster that loses data with its one AZ."""
    d = domain("noza", dedicated=True, master_count=3)
    scores = by_id(evaluate_opensearch_multiaz([d], {}, R))
    assert scores["noza"].score == 0.0
    assert "single AZ" in scores["noza"].reason


def test_unusual_dedicated_master_count_is_advisory_only():
    """Legacy even/low master counts (console now allows only 3/5) do not
    change the score, but the reason must flag them: 7.x+ ignores one node
    to keep the voting set odd, so 2 masters are effectively 1."""
    for count in (1, 2, 4):
        d = domain(f"m{count}", za=True, az_count=3, dedicated=True, master_count=count)
        s = by_id(evaluate_opensearch_multiaz([d], {}, R))[f"m{count}"]
        assert s.score == 100.0, f"{count} masters must not change the score"
        assert "master" in s.reason and "3 or 5" in s.reason
    ok = domain("m3", za=True, az_count=3, dedicated=True, master_count=3)
    assert "3 or 5" not in by_id(evaluate_opensearch_multiaz([ok], {}, R))["m3"].reason


def test_no_dedicated_masters_three_azs_score_20():
    d = domain("dn3", za=True, az_count=3, instance_count=3)
    scores = by_id(evaluate_opensearch_multiaz([d], {}, R))
    assert scores["dn3"].score == 100.0


def test_no_dedicated_masters_two_azs_score_10_for_split_brain_risk():
    """Rule 2: data nodes hold the master role; across only two AZs a
    partition can leave neither side with a clear majority."""
    d = domain("dn2", za=True, az_count=2, instance_count=4)
    scores = by_id(evaluate_opensearch_multiaz([d], {}, R))
    assert scores["dn2"].score == 50.0
    assert "split-brain" in scores["dn2"].reason


def test_no_dedicated_masters_single_az_scores_0():
    scores = by_id(evaluate_opensearch_multiaz([domain("dn1")], {}, R))
    assert scores["dn1"].score == 0.0


def test_za_enabled_without_config_counts_as_two_azs():
    """Old-style domains can report ZoneAwarenessEnabled with no
    ZoneAwarenessConfig; zone awareness without a count historically means
    two AZs, and must not be mistaken for one."""
    d = {"DomainName": "legacy", "ARN": "arn:legacy", "ClusterConfig": {
        "ZoneAwarenessEnabled": True, "DedicatedMasterEnabled": False, "InstanceCount": 2}}
    scores = by_id(evaluate_opensearch_multiaz([d], {}, R))
    assert scores["legacy"].score == 50.0


def test_exemption_via_tags_by_arn():
    d = domain("exempt")
    tags = {d["ARN"]: {"skip-multiaz-assessment": ""}}
    scores = by_id(evaluate_opensearch_multiaz([d], tags, R))
    assert scores["exempt"].score == 50.0 and scores["exempt"].exempted


def test_cross_region_name_match_with_connection_evidence():
    d = domain("logs-us-east-1")
    conns = [{
        "LocalDomainInfo": {"AWSDomainInformation": {"DomainName": "logs-us-east-1"}},
        "RemoteDomainInfo": {"AWSDomainInformation": {"DomainName": "logs-eu-west-1", "Region": "eu-west-1"}},
        "ConnectionStatus": {"StatusCode": "ACTIVE"},
    }]
    scores = by_id(evaluate_opensearch_crossregion([d], {}, {"eu-west-1": {"logs"}}, conns, R))
    assert scores["logs-us-east-1"].score == 100.0
    assert "heuristic" in scores["logs-us-east-1"].reason
    assert "ACTIVE cross-region connection" in scores["logs-us-east-1"].reason


def test_cross_region_no_match_scores_0():
    scores = by_id(evaluate_opensearch_crossregion(
        [domain("solo")], {}, {"eu-west-1": set()}, [], R))
    assert scores["solo"].score == 0.0
