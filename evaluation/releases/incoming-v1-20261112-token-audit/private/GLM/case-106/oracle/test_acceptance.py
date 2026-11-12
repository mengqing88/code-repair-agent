from target import normalize_path

def test_parent_pop():
    assert normalize_path("/a/b/../c") == "/a/c"

def test_parent_above_root():
    assert normalize_path("/../a") == "/a"

def test_pop_to_root():
    assert normalize_path("/a/..") == "/"
