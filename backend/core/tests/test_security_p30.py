"""P30 security review of the admin portal: the client-address bucket behind the per-address
limits and the shared admin login's lockout (F19), and the roll-number rule (F18)."""

import pytest

from tarn_core.domain.identity import address_bucket, lockout_source
from tarn_core.domain.tenancy import normalise_usn


@pytest.mark.parametrize(
    ("address", "bucket"),
    [
        ("198.51.100.7", "198.51.100.7"),
        ("2001:db8:7:1::5", "2001:db8:7:1::/64"),
        ("2001:db8:7:1:ffff:ffff:ffff:ffff", "2001:db8:7:1::/64"),
        ("::ffff:198.51.100.7", "198.51.100.7"),
        (" 2001:DB8:7:1::5 ", "2001:db8:7:1::/64"),
        ("unknown", "unknown"),
        ("", ""),
    ],
)
def test_address_bucket(address: str, bucket: str) -> None:
    assert address_bucket(address) == bucket


def test_one_lockout_counter_per_ipv6_64() -> None:
    assert lockout_source("2001:db8::1") == lockout_source("2001:db8::ffff:1")
    assert lockout_source("2001:db8::1") != lockout_source("2001:db8:0:1::1")
    assert lockout_source("") == ""
    assert "2001" not in lockout_source("2001:db8::1")  # a digest, never the address


@pytest.mark.parametrize(
    ("raw", "normal"),
    [
        ("1xx 21cs001", "1XX21CS001"),
        ("1xx21cs00\u0131", "1XX21CS00\u0131"),  # stays non-ASCII, so the checks refuse it
        ("1xx21cß01", "1XX21Cß01"),
    ],
)
def test_only_ascii_letters_are_upper_cased(raw: str, normal: str) -> None:
    assert normalise_usn(raw) == normal
