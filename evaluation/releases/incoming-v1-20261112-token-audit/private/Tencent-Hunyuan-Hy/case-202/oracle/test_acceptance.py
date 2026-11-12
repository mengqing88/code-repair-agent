from target import summarize


def test_iterator_input():
    assert summarize(iter([10, 20, 30])) == {"total": 60, "count": 3}


def test_generator_input():
    assert summarize(x for x in [1, 2, 3, 4]) == {"total": 10, "count": 4}


def test_single_element_iterator():
    assert summarize(iter([5])) == {"total": 5, "count": 1}
