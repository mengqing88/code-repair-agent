from target import top_words

def test_clear_winner():
    assert top_words("a b b c", 1) == ["b"]

def test_k_equals_all():
    assert top_words("x y", 2) == ["x", "y"]

def test_single_word():
    assert top_words("hi", 1) == ["hi"]
