"""Existing behaviour of the URL builder."""

from client import build_url


def test_paths_are_joined_without_duplicate_slashes():
    assert build_url("https://api.example.test/", "/v1/items") == "https://api.example.test/v1/items"


def test_a_trailing_slash_is_added_only_when_requested():
    assert build_url("https://api.example.test", "v1", trailing_slash=True) == "https://api.example.test/v1/"
    assert build_url("https://api.example.test", "v1") == "https://api.example.test/v1"


def test_none_parameters_are_dropped():
    assert build_url("https://api.example.test", "v1", {"page": 2, "filter": None}) == (
        "https://api.example.test/v1?page=2"
    )
