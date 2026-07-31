# tests/test_eks.py
from assessment.resilience.scanners.eks import evaluate_eks_crossregion

R = "ap-south-1"


def cluster(name, tags=None):
    return {"name": name, "tags": tags or {}}


def by_id(scores):
    return {s.resource_id: s for s in scores}


def test_cluster_name_match_scores_20():
    # 'abc-ap-south-1-abc' and 'abc-ap-south-2-abc' both strip to 'abc-abc'
    scores = by_id(evaluate_eks_crossregion(
        [cluster("abc-ap-south-1-abc")], {"ap-south-2": {"abc-abc"}}, R))
    assert scores["abc-ap-south-1-abc"].score == 100.0
    assert "heuristic" in scores["abc-ap-south-1-abc"].reason
    assert "ap-south-2" in scores["abc-ap-south-1-abc"].reason


def test_no_matching_cluster_scores_0():
    scores = by_id(evaluate_eks_crossregion(
        [cluster("payments-ap-south-1")], {"ap-south-2": {"billing"}}, R))
    assert scores["payments-ap-south-1"].score == 0.0
    assert "no EKS cluster matching" in scores["payments-ap-south-1"].reason


def test_exemption_tag_floors_to_10():
    scores = by_id(evaluate_eks_crossregion(
        [cluster("solo", tags={"skip-cross-region-assessment": "yes"})], {"ap-south-2": set()}, R))
    assert scores["solo"].score == 50.0 and scores["solo"].exempted


def test_multiple_standby_regions_all_listed():
    scores = by_id(evaluate_eks_crossregion(
        [cluster("abc-ap-south-1-abc")],
        {"ap-south-2": {"abc-abc"}, "eu-west-1": {"abc-abc"}}, R))
    reason = scores["abc-ap-south-1-abc"].reason
    assert "ap-south-2" in reason and "eu-west-1" in reason
