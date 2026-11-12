from target import backoff_schedule

def test_standard_backoff():
    assert backoff_schedule(2, 3) == [2, 4, 8]

def test_single_delay():
    assert backoff_schedule(2, 1) == [2]

def test_custom_factor():
    assert backoff_schedule(2, 2, factor=3) == [2, 6]
