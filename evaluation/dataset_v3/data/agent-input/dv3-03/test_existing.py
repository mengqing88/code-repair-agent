"""Existing behaviour of the duration parser."""

import pytest

from duration import parse_duration


def test_minutes_only():
    assert parse_duration("2m") == 120


def test_hours_only():
    assert parse_duration("1h") == 3600


def test_an_unknown_unit_is_rejected():
    with pytest.raises(ValueError):
        parse_duration("10x")
