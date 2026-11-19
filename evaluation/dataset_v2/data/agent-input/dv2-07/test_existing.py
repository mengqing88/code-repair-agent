"""Existing behaviour of the ACL check."""

from acl import check


def test_an_exact_rule_allows_the_path():
    assert check([{"pattern": "/docs", "effect": "allow"}], "/docs") == "allow"


def test_a_single_segment_wildcard_allows_the_path():
    assert check([{"pattern": "/docs/*", "effect": "allow"}], "/docs/report") == "allow"


def test_a_matching_rule_allows_a_nested_path():
    assert check([{"pattern": "/admin/*", "effect": "allow"}], "/admin/users") == "allow"
