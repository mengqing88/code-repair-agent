from target import sort_by_score


def test_two_digit_vs_one_digit():
    assert sort_by_score([("a", "10"), ("b", "2")]) == [("b", "2"), ("a", "10")]


def test_three_digit_scores():
    assert sort_by_score([("a", "9"), ("b", "10"), ("c", "100")]) == [
        ("a", "9"), ("b", "10"), ("c", "100")]


def test_mixed_numeric_order():
    assert sort_by_score([("x", "3"), ("y", "20"), ("z", "11")]) == [
        ("x", "3"), ("z", "11"), ("y", "20")]
