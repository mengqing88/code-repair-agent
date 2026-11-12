from target import validate_rows

def test_collects_all_errors():
    assert validate_rows([("a", 200), ("", 30), ("c", -1)]) == [
        (0, "bad age"), (1, "empty id"), (2, "bad age")]

def test_two_invalid_rows():
    assert validate_rows([("a", 20), ("", 30), ("c", 999)]) == [
        (1, "empty id"), (2, "bad age")]

def test_three_invalid_rows():
    assert validate_rows([("", 5), ("b", -3), ("", 17), ("d", 80)]) == [
        (0, "empty id"), (1, "bad age"), (2, "empty id")]
