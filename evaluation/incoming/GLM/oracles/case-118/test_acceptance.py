from target import top_words

def test_tie_alphabetical():
    assert top_words("b a b a c", 3) == ["a", "b", "c"]

def test_tie_not_insertion_order():
    assert top_words("zebra apple apple zebra mango", 3) == [
        "apple", "zebra", "mango"]

def test_numeric_tie():
    assert top_words("3 1 1 3 2 2", 3) == ["1", "2", "3"]
