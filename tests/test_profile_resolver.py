import pytest

from hascore.models import AccountSpec
from hascore.profile_resolver import ProfileResolutionError, load_profiles, resolve_profile

CONFIG = """
[profile prod-payments]
sso_account_id = 123456789012
sso_role_name = ReadOnly

[profile sandbox]
sso_account_id = 999999999999

[profile sandbox-admin]
sso_account_id = 999999999999

[profile no-sso]
region = us-east-1

[default]
sso_account_id = 111111111111
"""


@pytest.fixture
def mapping(tmp_path):
    cfg = tmp_path / "config"
    cfg.write_text(CONFIG)
    return load_profiles(cfg)


def test_load_profiles_maps_account_ids(mapping):
    assert mapping["123456789012"] == ["prod-payments"]
    assert mapping["111111111111"] == ["default"]
    assert sorted(mapping["999999999999"]) == ["sandbox", "sandbox-admin"]


def test_explicit_profile_wins(mapping):
    spec = AccountSpec("123456789012", ["us-east-1"], profile="override")
    assert resolve_profile(spec, mapping) == "override"


def test_single_match_resolves(mapping):
    spec = AccountSpec("123456789012", ["us-east-1"])
    assert resolve_profile(spec, mapping) == "prod-payments"


def test_no_match_raises(mapping):
    spec = AccountSpec("222222222222", ["us-east-1"])
    with pytest.raises(ProfileResolutionError, match="no profile"):
        resolve_profile(spec, mapping)


def test_multiple_matches_raise_and_name_candidates(mapping):
    spec = AccountSpec("999999999999", ["us-east-1"])
    with pytest.raises(ProfileResolutionError, match="sandbox"):
        resolve_profile(spec, mapping)
