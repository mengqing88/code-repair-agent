from target import get_header


def test_lowercase_query():
    assert get_header({"Content-Type": "text/plain"}, "content-type") == "text/plain"


def test_uppercase_query():
    assert get_header({"X-Token": "abc"}, "X-TOKEN") == "abc"


def test_mixed_case_query():
    assert get_header({"content-length": "42"}, "CONTENT-LENGTH") == "42"
