from target import has_access


def test_all_permissions_present():
    assert has_access(["a", "b"], ["a", "b"]) is True


def test_no_permission_matches():
    assert has_access(["x"], ["a", "b"]) is False


def test_single_required_permission():
    assert has_access(["a", "b", "c"], ["a"]) is True
