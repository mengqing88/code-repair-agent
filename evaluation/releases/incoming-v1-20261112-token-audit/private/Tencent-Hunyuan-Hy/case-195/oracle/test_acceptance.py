from target import make_callbacks


def test_two_callbacks_keep_own_value():
    cs = make_callbacks(["a", "b"])
    assert [f() for f in cs] == ["a", "b"]


def test_first_and_last_callbacks():
    cs = make_callbacks(["x", "y", "z"])
    assert cs[0]() == "x"
    assert cs[2]() == "z"


def test_integer_values():
    cs = make_callbacks([1, 2, 3])
    assert [f() for f in cs] == [1, 2, 3]
