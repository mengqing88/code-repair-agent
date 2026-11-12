from target import compare_versions

def test_equal():
    assert compare_versions("2.1", "2.1") == 0

def test_major_diff():
    assert compare_versions("3.0", "2.9") == 1

def test_older():
    assert compare_versions("1.0", "2.0") == -1
