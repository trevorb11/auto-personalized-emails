import pytest

from tools.parsing import (
    is_directory_domain, match_lender, normalize_business_name,
    normalize_date, normalize_domain, parse_money,
)


@pytest.mark.parametrize("value,expected", [
    (65000, 65000),
    ("50000", 50000),
    ("$50K", 50000),
    ("$50K-$100K/mo", 50000),
    ("$100K+/mo", 100000),
    ("$1.5M", 1_500_000),
    ("Under $10K/mo", 0),
    ("", 0),
    (None, 0),
    ("n/a", 0),
])
def test_parse_money(value, expected):
    assert parse_money(value) == expected


def test_match_lender_uses_word_boundaries():
    lenders = ["can capital", "unique funding", "unique funding solutions", "ondeck"]
    assert match_lender("American Capital Partners LLC", lenders) is None
    assert match_lender("CAN Capital, Inc.", lenders) == "can capital"
    assert match_lender("Unique Funding Solutions LLC", lenders) == "unique funding solutions"
    assert match_lender("", lenders) is None


@pytest.mark.parametrize("value,expected", [
    ("2026-03-15", "2026-03-15"),
    ("03/15/2026", "2026-03-15"),
    ("3/5/26", "2026-03-05"),
    ("2026-03-15T10:00:00", "2026-03-15"),
    ("20260315", "2026-03-15"),
    ("garbage", ""),
])
def test_normalize_date(value, expected):
    assert normalize_date(value) == expected


def test_normalize_business_name():
    assert normalize_business_name("Joe's Trucking, LLC") == "joes trucking"
    assert normalize_business_name("JOES TRUCKING INC.") == "joes trucking"


def test_domains():
    assert normalize_domain("https://www.Example.com/about") == "example.com"
    assert is_directory_domain("https://m.yelp.com/biz/foo")
    assert is_directory_domain("www.facebook.com")
    assert not is_directory_domain("joestrucking.com")
