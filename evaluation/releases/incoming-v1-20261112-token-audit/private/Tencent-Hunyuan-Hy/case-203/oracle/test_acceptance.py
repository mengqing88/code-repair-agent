from target import Session


def test_touch_renews_expiry():
    s = Session(0)
    s.touch(8)
    assert s.is_expired(12, 10) is False


def test_late_touch_renews_expiry():
    s = Session(0)
    s.touch(20)
    assert s.is_expired(25, 10) is False


def test_exact_ttl_after_touch_is_not_expired():
    s = Session(0)
    s.touch(10)
    assert s.is_expired(20, 10) is False
