from assessment.resilience.tags import EXEMPT_FLOOR, MULTIAZ_TAG, apply_exemption, tags_to_dict


def test_tags_to_dict_converts_key_value_list():
    tags = [{"Key": "env", "Value": "prod"}, {"Key": "skip-multiaz-assessment", "Value": ""}]
    assert tags_to_dict(tags) == {"env": "prod", "skip-multiaz-assessment": ""}


def test_tags_to_dict_handles_none_and_empty():
    assert tags_to_dict(None) == {}
    assert tags_to_dict([]) == {}


def test_the_floor_is_seventy():
    """70, not 50: an exemption is a reviewed decision, so it should not score near a
    resource nobody looked at. Not higher either — the tag is self-applied and
    unvalidated, so leaving only a small gap to 100 would make tagging the cheapest
    route to a good score."""
    assert EXEMPT_FLOOR == 70.0


def test_exemption_raises_failing_score_to_the_floor():
    score, exempted, suffix = apply_exemption(0.0, {"skip-multiaz-assessment": "true"}, MULTIAZ_TAG)
    assert score == 70.0
    assert exempted is True
    assert "skip-multiaz-assessment" in suffix


def test_the_stated_reason_quotes_the_floor_actually_applied():
    """The reason text used to hardcode its own number, so it could drift from the
    constant and tell a reader a score the tool never awarded."""
    score, _, suffix = apply_exemption(0.0, {"skip-multiaz-assessment": ""}, MULTIAZ_TAG)
    assert f"{score:g}/100" in suffix


def test_exemption_tag_key_is_case_insensitive_and_value_ignored():
    score, exempted, _ = apply_exemption(0.0, {"Skip-MultiAZ-Assessment": "whatever"}, MULTIAZ_TAG)
    assert (score, exempted) == (70.0, True)


def test_a_score_already_above_the_floor_is_left_alone():
    """EFS scores as two halves; a resource that passed one half sits above the floor
    and must not be pulled down to it."""
    score, exempted, suffix = apply_exemption(80.0, {"skip-multiaz-assessment": ""}, MULTIAZ_TAG)
    assert (score, exempted, suffix) == (80.0, False, "")


def test_exemption_is_floor_not_cap():
    score, exempted, suffix = apply_exemption(100.0, {"skip-multiaz-assessment": ""}, MULTIAZ_TAG)
    assert (score, exempted, suffix) == (100.0, False, "")


def test_no_tag_no_exemption():
    score, exempted, suffix = apply_exemption(0.0, {"env": "prod"}, MULTIAZ_TAG)
    assert (score, exempted, suffix) == (0.0, False, "")
