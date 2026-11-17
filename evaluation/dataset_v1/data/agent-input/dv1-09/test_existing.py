"""Existing behaviour of the configuration loader."""

from confmerge import load_config
from confmerge.merge import deep_merge


def test_a_file_value_replaces_a_flat_default():
    assert load_config({"log_level": "debug"})["log_level"] == "debug"


def test_no_overrides_keeps_the_defaults():
    assert load_config() == load_config({})


def test_a_new_top_level_key_is_added():
    config = load_config({"region": "eu"})
    assert config["region"] == "eu"
    assert config["db"]["host"] == "localhost"
