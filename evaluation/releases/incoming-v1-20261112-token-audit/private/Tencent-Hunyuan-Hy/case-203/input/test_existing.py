from target import Session


def test_not_expired_shortly_after_creation():
    s = Session(0)
    assert s.is_expired(5, 10) is False


def test_expired_without_touch():
    s = Session(0)
    assert s.is_expired(20, 10) is True


def test_touch_does_not_change_creation_time():
    s = Session(0)
    s.touch(3)
    assert s.created_at == 0
