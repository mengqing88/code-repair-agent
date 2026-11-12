from target import summarize


def test_list_input():
    assert summarize([10, 20, 30]) == {"total": 60, "count": 3}


def test_empty_list():
    assert summarize([]) == {"total": 0, "count": 0}


def test_single_element_list():
    assert summarize([7]) == {"total": 7, "count": 1}
