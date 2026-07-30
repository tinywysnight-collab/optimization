from hascore.scanners.asg import evaluate_asg_crossregion, evaluate_asg_multiaz, is_eks_asg

R = "us-east-1"


def group(name, azs, tags=()):
    return {
        "AutoScalingGroupName": name,
        "AvailabilityZones": list(azs),
        "Tags": [{"Key": k, "Value": v} for k, v in tags],
    }


def by_id(scores):
    return {s.resource_id: s for s in scores}


def test_multi_az_config_scores_20():
    scores = by_id(evaluate_asg_multiaz([group("web", ["us-east-1a", "us-east-1b"])], R))
    assert scores["web"].score == 100.0
    assert "2 AZ" in scores["web"].reason


def test_single_az_scores_0_and_exemption_floors():
    groups = [
        group("solo", ["us-east-1a"]),
        group("exempt", ["us-east-1a"], tags=[("skip-multiaz-assessment", "")]),
    ]
    scores = by_id(evaluate_asg_multiaz(groups, R))
    assert scores["solo"].score == 0.0
    assert scores["exempt"].score == 50.0 and scores["exempt"].exempted


def test_eks_origin_noted_in_reason():
    g = group("eks-ng-1234-uuid", ["us-east-1a", "us-east-1b"], tags=[("eks:cluster-name", "prod")])
    scores = by_id(evaluate_asg_multiaz([g], R))
    assert "EKS" in scores["eks-ng-1234-uuid"].reason


def test_is_eks_asg_detects_cluster_tag():
    assert is_eks_asg(group("eks-ng-1234-uuid", ["us-east-1a"], tags=[("eks:cluster-name", "prod")]))
    assert not is_eks_asg(group("plain", ["us-east-1a"]))


def test_cross_region_name_match_scores_20():
    g = group("myapp-us-east-1-web", ["us-east-1a"])
    standby = {"eu-west-1": {"myapp-web"}}
    scores = by_id(evaluate_asg_crossregion([g], standby, R))
    assert scores["myapp-us-east-1-web"].score == 100.0
    assert "heuristic" in scores["myapp-us-east-1-web"].reason
    assert "eu-west-1" in scores["myapp-us-east-1-web"].reason


def test_cross_region_no_match_scores_0():
    g = group("myapp-web", ["us-east-1a"])
    scores = by_id(evaluate_asg_crossregion([g], {"eu-west-1": {"other"}}, R))
    assert scores["myapp-web"].score == 0.0


def test_cross_region_skips_eks_node_group_asgs():
    # EKS is scored at the cluster level in its own dimension (spec §6)
    groups = [
        group("eks-40bbb26b-8679-eb64", ["us-east-1a"], tags=[("eks:cluster-name", "prod")]),
        group("myapp-web", ["us-east-1a"]),
    ]
    scores = by_id(evaluate_asg_crossregion(groups, {"eu-west-1": {"myapp-web"}}, R))
    assert set(scores) == {"myapp-web"}
