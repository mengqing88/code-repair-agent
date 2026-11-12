from target import compare_versions

def test_multidigit_segment():
    assert compare_versions("1.10.0", "1.9.0") == 1

def test_trailing_zero_equal():
    assert compare_versions("1.2", "1.2.0") == 0

def test_two_segment_multidigit():
    assert compare_versions("2.10", "2.9") == 1
