# tests/test_efs.py
from assessment.resilience.scanners.efs import evaluate_efs_crossregion, evaluate_efs_multiaz

R = "us-east-1"


def fs(fsid, one_zone=False, tags=()):
    d = {"FileSystemId": fsid, "Tags": [{"Key": k, "Value": v} for k, v in tags]}
    if one_zone:
        d["AvailabilityZoneId"] = "use1-az1"
    return d


def mt(az):
    return {"AvailabilityZoneId": az}


def by_id(scores):
    return {s.resource_id: s for s in scores}


def test_regional_with_two_az_mount_targets_scores_20():
    scores = by_id(evaluate_efs_multiaz([fs("fs-1")], {"fs-1": [mt("use1-az1"), mt("use1-az2")]}, R))
    assert scores["fs-1"].score == 100.0
    assert "Regional" in scores["fs-1"].reason and "2 AZ" in scores["fs-1"].reason


def test_regional_with_single_az_mount_target_scores_10():
    scores = by_id(evaluate_efs_multiaz([fs("fs-1")], {"fs-1": [mt("use1-az1")]}, R))
    assert scores["fs-1"].score == 50.0


def test_one_zone_scores_0():
    scores = by_id(evaluate_efs_multiaz([fs("fs-1", one_zone=True)], {"fs-1": [mt("use1-az1")]}, R))
    assert scores["fs-1"].score == 0.0
    assert "One Zone" in scores["fs-1"].reason


def test_exemption_applies_to_resource_total():
    scores = by_id(evaluate_efs_multiaz(
        [fs("fs-1", one_zone=True, tags=[("skip-multiaz-assessment", "")])], {"fs-1": []}, R))
    assert scores["fs-1"].score == 50.0 and scores["fs-1"].exempted


def test_cross_region_replication_scores_20():
    reps = [{"SourceFileSystemId": "fs-1", "Destinations": [{"Region": "eu-west-1"}]}]
    scores = by_id(evaluate_efs_crossregion([fs("fs-1")], reps, R, ["us-east-1", "eu-west-1"]))
    assert scores["fs-1"].score == 100.0
    assert "eu-west-1" in scores["fs-1"].reason


def test_replication_outside_the_designated_standby_scores_0():
    reps = [{"SourceFileSystemId": "fs-1", "Destinations": [{"Region": "ap-south-1"}]}]
    scores = by_id(evaluate_efs_crossregion(
        [fs("fs-1")], reps, R, ["us-east-1", "eu-west-1"]))
    assert scores["fs-1"].score == 0.0
    assert "ap-south-1" in scores["fs-1"].reason
    assert "eu-west-1" in scores["fs-1"].reason


def test_same_region_replication_does_not_count():
    reps = [{"SourceFileSystemId": "fs-1", "Destinations": [{"Region": "us-east-1"}]}]
    scores = by_id(evaluate_efs_crossregion([fs("fs-1")], reps, R, ["us-east-1", "eu-west-1"]))
    assert scores["fs-1"].score == 0.0


def test_mixed_az_id_and_name_do_not_double_count():
    """One mount target reporting the zone ID and another the zone name of the
    same physical AZ must not count as two AZs."""
    targets = [{"AvailabilityZoneId": "use1-az1"},
               {"AvailabilityZoneName": "us-east-1a"}]
    scores = by_id(evaluate_efs_multiaz([fs("fs-1")], {"fs-1": targets}, R))
    assert scores["fs-1"].score == 50.0  # storage 10 + mount targets 0
