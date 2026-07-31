from assessment.resilience.naming import strip_region


def test_strips_embedded_region_and_collapses_separators():
    assert strip_region("myapp-ap-south-1-nodes") == "myapp-nodes"


def test_strips_leading_region():
    assert strip_region("eu-west-1-cache") == "cache"


def test_underscore_separators():
    assert strip_region("my_app_us-east-1_x") == "my_app_x"


def test_name_without_region_unchanged():
    assert strip_region("plain-name") == "plain-name"


def test_region_lookalike_inside_word_not_stripped():
    # 'eb-tier-2' inside 'web-tier-2' must not match: token boundaries required
    assert strip_region("web-tier-2") == "web-tier-2"


def test_name_that_is_only_a_region_falls_back_to_original():
    assert strip_region("us-east-1") == "us-east-1"


def test_matching_is_case_insensitive():
    assert strip_region("MyApp-US-EAST-1-nodes") == "myapp-nodes"


def test_strips_govcloud_region():
    assert strip_region("app-us-gov-west-1-db") == "app-db"


def test_govcloud_pair_normalizes_identically():
    assert strip_region("app-us-gov-west-1") == strip_region("app-us-gov-east-1")
