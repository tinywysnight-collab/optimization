# tests/test_fsx.py
from hascore.scanners.fsx import evaluate_fsx_crossregion, evaluate_fsx_multiaz, fsx_match_value

R = "us-east-1"


def fs(fsid, fstype="WINDOWS", deployment="MULTI_AZ_1", tags=()):
    d = {"FileSystemId": fsid, "FileSystemType": fstype,
         "Tags": [{"Key": k, "Value": v} for k, v in tags]}
    if fstype == "WINDOWS":
        d["WindowsConfiguration"] = {"DeploymentType": deployment}
    return d


def by_id(scores):
    return {s.resource_id: s for s in scores}


def test_windows_multi_az_scores_20():
    scores = by_id(evaluate_fsx_multiaz([fs("fs-1")], R))
    assert scores["fs-1"].score == 100.0
    assert "MULTI_AZ_1" in scores["fs-1"].reason


def test_windows_single_az_scores_0_and_exemption_floors():
    filesystems = [
        fs("fs-1", deployment="SINGLE_AZ_2"),
        fs("fs-2", deployment="SINGLE_AZ_2", tags=[("skip-multiaz", "")]),
    ]
    scores = by_id(evaluate_fsx_multiaz(filesystems, R))
    assert scores["fs-1"].score == 0.0
    assert scores["fs-2"].score == 50.0 and scores["fs-2"].exempted


def test_non_windows_types_are_na_with_explicit_note():
    scores = by_id(evaluate_fsx_multiaz([fs("fs-l", fstype="LUSTRE")], R))
    assert scores["fs-l"].score is None
    assert "FSx for Windows only" in scores["fs-l"].reason
    assert "LUSTRE" in scores["fs-l"].reason


# --- cross-region ---

def test_match_value_is_the_name_tag():
    assert fsx_match_value(fs("fs-1", tags=[("Name", "share-us-east-1")])) == "share-us-east-1"
    assert fsx_match_value(fs("fs-1")) is None


def test_cross_region_name_match_scores_20():
    filesystems = [fs("fs-1", tags=[("Name", "share-us-east-1")])]
    scores = by_id(evaluate_fsx_crossregion(filesystems, {"eu-west-1": {"share"}}, R))
    assert scores["fs-1"].score == 100.0
    assert "heuristic" in scores["fs-1"].reason
    assert "eu-west-1" in scores["fs-1"].reason


def test_cross_region_no_match_scores_0():
    filesystems = [fs("fs-1", tags=[("Name", "share")])]
    scores = by_id(evaluate_fsx_crossregion(filesystems, {"eu-west-1": {"other"}}, R))
    assert scores["fs-1"].score == 0.0


def test_windows_without_name_tag_scores_0_not_na():
    scores = by_id(evaluate_fsx_crossregion([fs("fs-1")], {"eu-west-1": {"share"}}, R))
    assert scores["fs-1"].score == 0.0
    assert "no 'Name' tag" in scores["fs-1"].reason


def test_cross_region_exemption_floors_to_10():
    filesystems = [fs("fs-1", tags=[("Name", "share"), ("skip-cross-region", "")])]
    scores = by_id(evaluate_fsx_crossregion(filesystems, {"eu-west-1": set()}, R))
    assert scores["fs-1"].score == 50.0 and scores["fs-1"].exempted


def test_cross_region_non_windows_is_na():
    scores = by_id(evaluate_fsx_crossregion(
        [fs("fs-l", fstype="LUSTRE", tags=[("Name", "scratch")])], {"eu-west-1": {"scratch"}}, R))
    assert scores["fs-l"].score is None
    assert "FSx for Windows only" in scores["fs-l"].reason
