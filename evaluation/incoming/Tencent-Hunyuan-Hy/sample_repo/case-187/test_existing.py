from target import rank


def test_distinct_scores():
    assert rank([("a", 10), ("b", 20)]) == [("b", 20), ("a", 10)]


def test_empty_players():
    assert rank([]) == []


def test_single_player():
    assert rank([("a", 5)]) == [("a", 5)]
