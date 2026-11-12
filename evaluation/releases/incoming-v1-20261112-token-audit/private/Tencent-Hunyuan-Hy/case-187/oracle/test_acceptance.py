from target import rank


def test_tie_broken_by_name():
    assert rank([("bob", 10), ("alice", 10)]) == [("alice", 10), ("bob", 10)]


def test_tie_with_other_scores():
    assert rank([("c", 5), ("b", 9), ("a", 9)]) == [
        ("a", 9), ("b", 9), ("c", 5)]


def test_multiple_ties():
    assert rank([("zoe", 7), ("amy", 7), ("bob", 3)]) == [
        ("amy", 7), ("zoe", 7), ("bob", 3)]
