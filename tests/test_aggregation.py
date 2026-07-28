from hascore.aggregation import compute_account_score, compute_service_scores, finalize_dimension
from hascore.models import MULTI_AZ, DimensionResult, ResourceScore


def rs(service, score):
    return ResourceScore(service=service, resource_id="r", region="us-east-1", score=score, reason="x")


def test_service_scores_average_within_service():
    scores = compute_service_scores([rs("rds", 20.0), rs("rds", 0.0), rs("asg", 20.0)])
    assert scores == {"rds": 10.0, "asg": 20.0}


def test_service_with_only_na_resources_is_na():
    scores = compute_service_scores([rs("fsx", None), rs("fsx", None)])
    assert scores == {"fsx": None}


def test_na_resources_excluded_from_service_mean():
    scores = compute_service_scores([rs("elasticache", 20.0), rs("elasticache", None)])
    assert scores == {"elasticache": 20.0}


def test_account_score_equal_weight_mean_of_non_na_dimensions():
    assert compute_account_score({"rds": 10.0, "asg": 20.0, "fsx": None}) == 15.0


def test_account_score_all_na_is_na():
    assert compute_account_score({"fsx": None}) is None
    assert compute_account_score({}) is None


def test_finalize_dimension_populates_fields():
    dim = DimensionResult(MULTI_AZ, resources=[rs("rds", 20.0), rs("rds", 10.0)])
    finalize_dimension(dim)
    assert dim.service_scores == {"rds": 15.0}
    assert dim.account_score == 15.0
