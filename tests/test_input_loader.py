import json

import pytest

from hascore.input_loader import InputError, load_accounts


def write(tmp_path, payload):
    p = tmp_path / "accounts.json"
    p.write_text(json.dumps(payload))
    return p


def test_loads_full_account_entry(tmp_path):
    path = write(tmp_path, {"accounts": [{
        "account_id": "123456789012",
        "pattern_id": "PATTERN-A1",
        "regions": ["us-east-1", "eu-west-1"],
        "application": {"name": "payments"},
        "profile": "prod-payments",
    }]})
    specs = load_accounts(path)
    assert len(specs) == 1
    s = specs[0]
    assert s.account_id == "123456789012"
    assert s.regions == ["us-east-1", "eu-west-1"]
    assert s.pattern_id == "PATTERN-A1"
    assert s.application == {"name": "payments"}
    assert s.profile == "prod-payments"


def test_optional_fields_default(tmp_path):
    path = write(tmp_path, {"accounts": [{
        "account_id": "123456789012", "regions": ["us-east-1"],
    }]})
    s = load_accounts(path)[0]
    assert s.pattern_id is None and s.application == {} and s.profile is None


def test_rejects_bad_account_id(tmp_path):
    path = write(tmp_path, {"accounts": [{"account_id": "12345", "regions": ["us-east-1"]}]})
    with pytest.raises(InputError, match="account_id"):
        load_accounts(path)


def test_rejects_empty_regions(tmp_path):
    path = write(tmp_path, {"accounts": [{"account_id": "123456789012", "regions": []}]})
    with pytest.raises(InputError, match="regions"):
        load_accounts(path)


def test_rejects_missing_accounts_key(tmp_path):
    path = write(tmp_path, {})
    with pytest.raises(InputError, match="accounts"):
        load_accounts(path)
