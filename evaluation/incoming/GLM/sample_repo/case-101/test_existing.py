from target import backoff_schedule

def test_zero_count():
    assert backoff_schedule(2, 0) == []

def test_flat_factor_one():
    assert backoff_schedule(3, 4, factor=1) == [3, 3, 3, 3]

def test_flat_factor_one_len():
    r = backoff_schedule(5, 3, factor=1)
    assert len(r) == 3 and all(x == 5 for x in r)
