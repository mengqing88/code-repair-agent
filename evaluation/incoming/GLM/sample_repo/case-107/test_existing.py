from target import parse_fields

def test_plain_line():
    assert parse_fields("ann,eng,7") == ("ann", "eng", "7")

def test_another_line():
    assert parse_fields("bob,ops,42") == ("bob", "ops", "42")

def test_single_chars():
    assert parse_fields("a,b,c") == ("a", "b", "c")
