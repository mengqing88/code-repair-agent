from target import validate_rows

def test_all_valid():
    assert validate_rows([("a", 20), ("b", 30)]) == []

def test_single_invalid_first():
    assert validate_rows([("", 20), ("b", 30)]) == [(0, "empty id")]

def test_empty_rows():
    assert validate_rows([]) == []
