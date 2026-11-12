from target import parse_fields

def test_spaces_after_commas():
    assert parse_fields("ann, eng, 7") == ("ann", "eng", "7")

def test_spaces_around_fields():
    assert parse_fields("  bob ,  ops , 42 ") == ("bob", "ops", "42")

def test_tabs():
    assert parse_fields("\tcar\t,dev,9") == ("car", "dev", "9")
