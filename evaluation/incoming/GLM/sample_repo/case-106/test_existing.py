from target import normalize_path

def test_plain_path():
    assert normalize_path("/a/b/c") == "/a/b/c"

def test_extra_slashes():
    assert normalize_path("/a//b/") == "/a/b"

def test_dot_segments():
    assert normalize_path("/a/./b") == "/a/b"
