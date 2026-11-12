from target import sort_by_score


def test_single_digit_scores():
    assert sort_by_score([("b", "2"), ("a", "1")]) == [("a", "1"), ("b", "2")]


def test_empty_rows():
    assert sort_by_score([]) == []


def test_equal_scores_keep_input_order():
    assert sort_by_score([("b", "5"), ("a", "5")]) == [("b", "5"), ("a", "5")]
