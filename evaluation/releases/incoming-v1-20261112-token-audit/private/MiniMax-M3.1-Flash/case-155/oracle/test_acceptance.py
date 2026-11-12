import pytest

from target import resolve_setting


def test_empty_string_override_is_honoured():
    assert resolve_setting("mode", {"mode": ""}, {"mode": "fallback"}) == ""


def test_zero_override_is_honoured():
    assert resolve_setting("retries", {"retries": 0}, {"retries": 7}) == 0


def test_false_override_is_honoured():
    assert resolve_setting("enabled", {"enabled": False}, {"enabled": True}) is False
