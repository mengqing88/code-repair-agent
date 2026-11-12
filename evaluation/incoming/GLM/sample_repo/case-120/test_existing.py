from target import format_duration

def test_two_fifteen_thirty():
    assert format_duration(8130) == "2:15:30"

def test_ten_twenty_thirty():
    assert format_duration(37230) == "10:20:30"

def test_twelve_colon_format():
    r = format_duration(45295)
    assert r.count(":") == 2 and r == "12:34:55"
