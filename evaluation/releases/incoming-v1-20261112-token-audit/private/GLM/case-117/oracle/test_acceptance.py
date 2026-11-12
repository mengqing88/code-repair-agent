from target import ReleaseManager

def test_no_double_release():
    rm = ReleaseManager()
    rm.add("b1", 5)
    rm.release_expired(10)
    assert rm.release_expired(10) == []

def test_only_new_released_later():
    rm = ReleaseManager()
    rm.add("b1", 5)
    rm.release_expired(10)
    rm.add("b2", 8)
    assert rm.release_expired(12) == ["b2"]

def test_repeat_call_count():
    rm = ReleaseManager()
    rm.add("b1", 1)
    rm.add("b2", 2)
    rm.release_expired(3)
    assert rm.release_expired(100) == []
