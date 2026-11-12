from target import has_access


def test_partial_permissions_must_fail():
    assert has_access(["a"], ["a", "b"]) is False


def test_missing_one_of_three():
    assert has_access(["a", "b"], ["a", "b", "c"]) is False


def test_role_names_partial():
    assert has_access(["read"], ["read", "write", "admin"]) is False
