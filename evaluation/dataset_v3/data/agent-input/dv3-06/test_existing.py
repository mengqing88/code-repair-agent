"""Existing behaviour of the settings resolver."""

from config_layers import resolve


def test_environment_wins_over_the_file():
    assert resolve({"timeout": 10}, {"timeout": 20})["timeout"] == 20


def test_none_means_not_set():
    assert resolve({"timeout": None})["timeout"] == 30


def test_defaults_are_used_without_overrides():
    assert resolve() == {"retries": 3, "region": "us", "timeout": 30}
