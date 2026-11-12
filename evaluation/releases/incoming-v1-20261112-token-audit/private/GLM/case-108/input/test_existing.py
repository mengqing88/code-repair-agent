from target import insert_position

def test_middle():
    assert insert_position([1, 3, 5], 4) == 2

def test_front():
    assert insert_position([1, 3, 5], 0) == 0

def test_end():
    assert insert_position([1, 3, 5], 9) == 3
