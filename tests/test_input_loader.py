import pytest

from hascore.input_loader import InputError, parse_accounts


def test_parses_full_account_entry():
    specs = parse_accounts({"accounts": [{
        "account_id": "123456789012",
        "pattern_id": "PATTERN-A1",
        "regions": ["us-east-1", "eu-west-1"],
        "application": {"name": "payments"},
        "role_name": "LegacyAuditRole",
    }]})
    assert len(specs) == 1
    s = specs[0]
    assert s.account_id == "123456789012"
    assert s.regions == ["us-east-1", "eu-west-1"]
    assert s.pattern_id == "PATTERN-A1"
    assert s.application == {"name": "payments"}
    assert s.role_name == "LegacyAuditRole"


def test_optional_fields_default():
    s = parse_accounts({"accounts": [{
        "account_id": "123456789012", "regions": ["us-east-1"],
    }]})[0]
    assert s.pattern_id is None and s.application == {} and s.role_name is None


def test_rejects_bad_account_id():
    with pytest.raises(InputError, match="account_id"):
        parse_accounts({"accounts": [{"account_id": "12345", "regions": ["us-east-1"]}]})


def test_rejects_empty_regions():
    with pytest.raises(InputError, match="regions"):
        parse_accounts({"accounts": [{"account_id": "123456789012", "regions": []}]})


def test_rejects_missing_accounts_key():
    with pytest.raises(InputError, match="accounts"):
        parse_accounts({})


def test_rejects_accounts_that_is_not_a_list():
    with pytest.raises(InputError, match="accounts"):
        parse_accounts({"accounts": {}})


def test_rejects_payload_that_is_not_a_mapping():
    with pytest.raises(InputError, match="accounts"):
        parse_accounts([{"account_id": "123456789012", "regions": ["us-east-1"]}])


def test_rejects_an_account_entry_that_is_not_a_mapping():
    with pytest.raises(InputError, match=r"accounts\[0\]"):
        parse_accounts({"accounts": [1]})


def test_rejects_a_non_string_pattern_id():
    with pytest.raises(InputError, match="pattern_id"):
        parse_accounts({"accounts": [{
            "account_id": "123456789012",
            "regions": ["us-east-1"],
            "pattern_id": 1,
        }]})


def test_preserves_falsey_application_metadata_verbatim():
    spec = parse_accounts({"accounts": [{
        "account_id": "123456789012",
        "regions": ["us-east-1"],
        "application": [],
    }]})[0]
    assert spec.application == []


def test_environment_is_parsed_and_optional():
    with_env = parse_accounts({"accounts": [{
        "account_id": "123456789012", "regions": ["us-east-1"], "environment": "production"}]})[0]
    assert with_env.environment == "production"
    without = parse_accounts({"accounts": [{
        "account_id": "123456789012", "regions": ["us-east-1"]}]})[0]
    assert without.environment is None


def test_environment_must_be_a_string():
    with pytest.raises(InputError, match="environment"):
        parse_accounts({"accounts": [{
            "account_id": "123456789012", "regions": ["us-east-1"], "environment": 7}]})


def test_environment_vocabulary_is_the_callers_own():
    """Display-only: no fixed list, no normalisation, whatever the caller sends."""
    for value in ("production", "UAT", "dev-2", "pre-prod / dr"):
        s = parse_accounts({"accounts": [{
            "account_id": "123456789012", "regions": ["us-east-1"], "environment": value}]})[0]
        assert s.environment == value
