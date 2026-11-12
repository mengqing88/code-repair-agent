from target import make_callbacks


def test_single_callback():
    assert make_callbacks(["a"])[0]() == "a"


def test_empty_names():
    assert make_callbacks([]) == []


def test_length_matches_input():
    assert len(make_callbacks(["a", "b", "c"])) == 3
