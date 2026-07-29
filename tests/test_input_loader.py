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
