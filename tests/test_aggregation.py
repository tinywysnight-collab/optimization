from assessment.resilience.aggregation import (
    compute_account_score,
    compute_service_scores,
    compute_service_scores_by_region,
    finalize_dimension,
)
from assessment.resilience.models import MULTI_AZ, DimensionResult, ResourceScore


def rs(service, score):
    return ResourceScore(service=service, resource_id="r", region="us-east-1", score=score, reason="x")


def test_service_scores_average_within_service():
    scores = compute_service_scores([rs("rds", 100.0), rs("rds", 0.0), rs("asg", 100.0)])
    assert scores == {"rds": 50.0, "asg": 100.0}


def test_service_with_only_na_resources_is_na():
    scores = compute_service_scores([rs("fsx", None), rs("fsx", None)])
    assert scores == {"fsx": None}


def test_na_resources_excluded_from_service_mean():
    scores = compute_service_scores([rs("elasticache", 100.0), rs("elasticache", None)])
    assert scores == {"elasticache": 100.0}


def test_account_score_equal_weight_mean_of_non_na_dimensions():
    assert compute_account_score({"rds": 50.0, "asg": 100.0, "fsx": None}) == 75.0


def test_account_score_all_na_is_na():
    assert compute_account_score({"fsx": None}) is None
    assert compute_account_score({}) is None


def test_finalize_dimension_populates_fields():
    dim = DimensionResult(MULTI_AZ, resources=[rs("rds", 100.0), rs("rds", 50.0)])
    finalize_dimension(dim)
    assert dim.service_scores == {"rds": 75.0}
    assert dim.account_score == 75.0


def rs_in(service, region, score):
    return ResourceScore(service=service, resource_id="r", region=region, score=score, reason="x")


def test_per_region_breakdown_locates_the_failing_region():
    resources = [rs_in("rds", "ap-south-1", 100.0), rs_in("rds", "eu-west-1", 0.0)]
    assert compute_service_scores_by_region(resources) == {
        "rds": {"ap-south-1": 100.0, "eu-west-1": 0.0}}


def test_per_region_breakdown_keeps_na_semantics_within_a_region():
    resources = [rs_in("fsx", "ap-south-1", None), rs_in("fsx", "eu-west-1", 100.0)]
    assert compute_service_scores_by_region(resources) == {
        "fsx": {"ap-south-1": None, "eu-west-1": 100.0}}


def test_per_region_breakdown_averages_within_each_region():
    resources = [rs_in("rds", "ap-south-1", 100.0), rs_in("rds", "ap-south-1", 0.0),
                 rs_in("rds", "eu-west-1", 50.0)]
    assert compute_service_scores_by_region(resources) == {
        "rds": {"ap-south-1": 50.0, "eu-west-1": 50.0}}


def test_finalize_omits_the_breakdown_for_a_single_region():
    """With one region it would only restate service_scores."""
    dim = DimensionResult(MULTI_AZ, resources=[rs_in("rds", "ap-south-1", 100.0)])
    finalize_dimension(dim)
    assert dim.service_scores_by_region == {}


def test_finalize_populates_the_breakdown_across_regions():
    dim = DimensionResult(MULTI_AZ, resources=[
        rs_in("rds", "ap-south-1", 100.0), rs_in("rds", "eu-west-1", 0.0)])
    finalize_dimension(dim)
    assert dim.service_scores == {"rds": 50.0}, "the pooled score must not change"
    assert dim.account_score == 50.0
    assert dim.service_scores_by_region == {"rds": {"ap-south-1": 100.0, "eu-west-1": 0.0}}
