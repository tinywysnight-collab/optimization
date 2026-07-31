"""The Cross-Region dimension is gated by pattern_id, not by region count."""
import pytest

from assessment.resilience.input_loader import InputError, parse_accounts
from assessment.resilience.models import CROSS_REGION_PATTERN, AccountSpec


def spec(pattern, regions=("ap-south-1", "ap-south-2")):
    return AccountSpec("123456789012", list(regions), pattern_id=pattern)


# --- which accounts are in scope ---

def test_pattern_carrying_the_marker_requires_cross_region():
    assert spec("GS-001").cross_region_required
    assert spec("PATTERN-GS-001-B").cross_region_required, "must match as a substring"


def test_marker_match_is_case_insensitive():
    assert spec("gs-001").cross_region_required


def test_other_patterns_are_out_of_scope():
    for pattern in ("GS-002", "PATTERN-A1", "", None):
        assert not spec(pattern).cross_region_required, pattern


def test_marker_constant_is_the_documented_one():
    assert CROSS_REGION_PATTERN == "GS-001"


# --- which region is the standby ---

def test_the_standby_is_the_second_region_only():
    """Defence in depth: even if a third region reached the model, only the
    second is a standby candidate. The loader rejects such payloads outright."""
    s = spec("GS-001", regions=("ap-south-1", "ap-south-2", "eu-west-1"))
    assert s.standby_regions == ["ap-south-2"]


def test_out_of_scope_accounts_have_no_standby_even_with_several_regions():
    s = spec("PATTERN-A1", regions=("ap-south-1", "ap-south-2"))
    assert s.standby_regions == []


# --- a GS-001 account without a second region is an input error ---

def test_gs001_without_a_second_region_is_rejected_at_parse_time():
    payload = {"accounts": [{
        "account_id": "123456789012", "regions": ["ap-south-1"], "pattern_id": "GS-001"}]}
    with pytest.raises(InputError, match="GS-001"):
        parse_accounts(payload)


def test_the_rejection_names_the_account_and_the_missing_region():
    payload = {"accounts": [
        {"account_id": "111111111111", "regions": ["ap-south-1", "ap-south-2"]},
        {"account_id": "222222222222", "regions": ["ap-south-1"], "pattern_id": "GS-001"},
    ]}
    with pytest.raises(InputError) as err:
        parse_accounts(payload)
    message = str(err.value)
    assert "222222222222" in message or "accounts[1]" in message
    assert "standby" in message or "second region" in message


def test_gs001_with_a_second_region_parses():
    payload = {"accounts": [{
        "account_id": "123456789012", "regions": ["ap-south-1", "ap-south-2"],
        "pattern_id": "GS-001"}]}
    assert parse_accounts(payload)[0].standby_regions == ["ap-south-2"]


def test_gs001_rejects_the_primary_region_as_its_own_standby():
    payload = {"accounts": [{
        "account_id": "123456789012", "regions": ["ap-south-1", "ap-south-1"],
        "pattern_id": "GS-001"}]}
    with pytest.raises(InputError, match="distinct"):
        parse_accounts(payload)


def test_gs001_with_a_third_region_is_rejected():
    """The pattern names exactly one primary and one standby; a third region
    means the payload disagrees with the pattern, so it is not silently ignored."""
    payload = {"accounts": [{
        "account_id": "123456789012", "pattern_id": "GS-001",
        "regions": ["ap-south-1", "ap-south-2", "eu-west-1"]}]}
    with pytest.raises(InputError, match="exactly two"):
        parse_accounts(payload)


def test_accounts_outside_the_pattern_may_list_any_number_of_regions():
    """The two-region rule belongs to the pattern, not to every account."""
    payload = {"accounts": [{
        "account_id": "123456789012", "pattern_id": "PATTERN-A1",
        "regions": ["ap-south-1", "ap-south-2", "eu-west-1"]}]}
    assert parse_accounts(payload)[0].standby_regions == []


def test_a_single_region_account_outside_the_pattern_is_fine():
    payload = {"accounts": [{
        "account_id": "123456789012", "regions": ["ap-south-1"], "pattern_id": "PATTERN-A1"}]}
    assert parse_accounts(payload)[0].standby_regions == []


# --- PTM: independent regions, all assessed for Multi-AZ, never Cross-Region ---

def test_ptm_marks_every_region_for_multiaz():
    s = spec("PTM", regions=("ap-south-1", "eu-west-1", "us-east-1"))
    assert s.multiaz_regions == ["ap-south-1", "eu-west-1", "us-east-1"]


def test_ptm_is_never_in_cross_region_scope():
    """Independent deployments are not standbys for one another."""
    s = spec("PTM", regions=("ap-south-1", "eu-west-1"))
    assert not s.cross_region_required
    assert s.standby_regions == []


def test_ptm_marker_is_a_case_insensitive_substring():
    assert spec("app-ptm-002", regions=("ap-south-1",)).independent_regions
    assert spec("PATTERN-PTM", regions=("ap-south-1",)).independent_regions
    assert not spec("PATTERN-A1", regions=("ap-south-1",)).independent_regions


def test_ptm_accepts_any_region_count_including_one():
    for regions in (("ap-south-1",), ("ap-south-1", "eu-west-1", "us-east-1", "sa-east-1")):
        s = parse_accounts({"accounts": [{
            "account_id": "123456789012", "pattern_id": "PTM", "regions": list(regions)}]})[0]
        assert s.multiaz_regions == list(regions)


def test_non_ptm_accounts_are_scanned_in_the_primary_region_only():
    """A GS-001 standby is a copy, not a separate estate."""
    assert spec("GS-001", regions=("ap-south-1", "ap-south-2")).multiaz_regions == ["ap-south-1"]
    assert spec("PATTERN-A1", regions=("ap-south-1", "eu-west-1")).multiaz_regions == ["ap-south-1"]


def test_a_pattern_carrying_both_markers_is_rejected():
    """An account cannot both pair one standby and run independent regions."""
    payload = {"accounts": [{
        "account_id": "123456789012", "pattern_id": "GS-001-PTM",
        "regions": ["ap-south-1", "ap-south-2"]}]}
    with pytest.raises(InputError, match="both"):
        parse_accounts(payload)
