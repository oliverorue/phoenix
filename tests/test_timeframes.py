"""Tests de normalización centralizada de intervalos."""

import pytest

from bot.core.timeframes import interval_minutes, normalize_interval


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("15", "15m"),
        ("60", "1h"),
        ("1h", "1h"),
        ("15m", "15m"),
        ("60m", "1h"),
        ("120", "2h"),
        ("240", "4h"),
        ("1440", "1d"),
        (15, "15m"),
        (60, "1h"),
        ("1M", "1M"),
    ],
)
def test_normalize_interval(raw, expected):
    assert normalize_interval(raw) == expected


def test_normalize_interval_unknown_returns_lowercase():
    assert normalize_interval("7m") == "7m"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("15", 15),
        ("60", 60),
        ("1h", 60),
        ("15m", 15),
        ("1d", 1440),
        ("1M", 43200),
    ],
)
def test_interval_minutes(raw, expected):
    assert interval_minutes(raw) == expected
