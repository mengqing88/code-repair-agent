from target import format_duration

def test_one_hour_one():
    assert format_duration(3661) == "1:01:01"

def test_one_minute():
    assert format_duration(60) == "0:01:00"

def test_zero():
    assert format_duration(0) == "0:00:00"
