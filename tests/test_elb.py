# tests/test_elb.py
from hascore.scanners.elb import evaluate_elb_crossregion, evaluate_elb_multiaz

R = "us-east-1"


def lb(name, lb_type="application", tags=None, azs=("us-east-1a",)):
    return {"name": name, "type": lb_type, "tags": tags or {}, "azs": list(azs)}


def by_id(scores):
    return {s.resource_id: s for s in scores}


# --- multi-AZ (NLB only) ---

def test_nlb_across_two_azs_scores_20():
    scores = by_id(evaluate_elb_multiaz(
        [lb("nlb-1", lb_type="network", azs=["us-east-1a", "us-east-1b"])], R))
    assert scores["nlb-1"].score == 20.0
    assert "2 AZ" in scores["nlb-1"].reason


def test_single_az_nlb_scores_0_and_exemption_floors():
    lbs = [
        lb("nlb-solo", lb_type="network"),
        lb("nlb-exempt", lb_type="network", tags={"disable-multiaz": ""}),
    ]
    scores = by_id(evaluate_elb_multiaz(lbs, R))
    assert scores["nlb-solo"].score == 0.0
    assert scores["nlb-exempt"].score == 10.0 and scores["nlb-exempt"].exempted


def test_alb_is_na_because_aws_enforces_two_azs():
    scores = by_id(evaluate_elb_multiaz(
        [lb("alb-1", lb_type="application", azs=["us-east-1a", "us-east-1b"])], R))
    assert scores["alb-1"].score is None
    assert "enforces" in scores["alb-1"].reason


def test_classic_and_gateway_are_na():
    scores = by_id(evaluate_elb_multiaz(
        [lb("clb-1", lb_type="classic"), lb("gwlb-1", lb_type="gateway")], R))
    assert scores["clb-1"].score is None and scores["gwlb-1"].score is None
    assert "NLB only" in scores["clb-1"].reason


# --- cross-region (all types) ---

def test_name_match_scores_20_with_heuristic_reason():
    scores = by_id(evaluate_elb_crossregion(
        [lb("myapp-us-east-1-alb")], {"eu-west-1": {"myapp-alb"}}, R))
    assert scores["myapp-us-east-1-alb"].score == 20.0
    assert "heuristic" in scores["myapp-us-east-1-alb"].reason
    assert "eu-west-1" in scores["myapp-us-east-1-alb"].reason


def test_no_match_scores_0():
    scores = by_id(evaluate_elb_crossregion(
        [lb("myapp-alb")], {"eu-west-1": {"other"}}, R))
    assert scores["myapp-alb"].score == 0.0


def test_exemption_tag_floors_to_10():
    scores = by_id(evaluate_elb_crossregion(
        [lb("solo", tags={"disable-crossregion": ""})], {"eu-west-1": set()}, R))
    assert scores["solo"].score == 10.0 and scores["solo"].exempted


def test_type_appears_in_reason():
    scores = by_id(evaluate_elb_crossregion(
        [lb("legacy", lb_type="classic")], {"eu-west-1": set()}, R))
    assert "classic" in scores["legacy"].reason
