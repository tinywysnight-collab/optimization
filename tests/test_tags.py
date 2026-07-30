from hascore.tags import MULTIAZ_TAG, apply_exemption, tags_to_dict


def test_tags_to_dict_converts_key_value_list():
    tags = [{"Key": "env", "Value": "prod"}, {"Key": "skip-multiaz", "Value": ""}]
    assert tags_to_dict(tags) == {"env": "prod", "skip-multiaz": ""}


def test_tags_to_dict_handles_none_and_empty():
    assert tags_to_dict(None) == {}
    assert tags_to_dict([]) == {}


def test_exemption_raises_failing_score_to_floor_of_10():
    score, exempted, suffix = apply_exemption(0.0, {"skip-multiaz": "true"}, MULTIAZ_TAG)
    assert score == 50.0
    assert exempted is True
    assert "skip-multiaz" in suffix


def test_exemption_tag_key_is_case_insensitive_and_value_ignored():
    score, exempted, _ = apply_exemption(0.0, {"Skip-MultiAZ": "whatever"}, MULTIAZ_TAG)
    assert (score, exempted) == (50.0, True)


def test_exemption_is_floor_not_cap():
    score, exempted, suffix = apply_exemption(100.0, {"skip-multiaz": ""}, MULTIAZ_TAG)
    assert (score, exempted, suffix) == (100.0, False, "")


def test_no_tag_no_exemption():
    score, exempted, suffix = apply_exemption(0.0, {"env": "prod"}, MULTIAZ_TAG)
    assert (score, exempted, suffix) == (0.0, False, "")
