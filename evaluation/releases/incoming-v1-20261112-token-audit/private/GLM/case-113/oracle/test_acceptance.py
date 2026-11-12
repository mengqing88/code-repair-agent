from target import SessionManager

def test_touch_keeps_alive():
    sm = SessionManager(10)
    sm.create("s1", 0)
    sm.touch("s1", 5)
    assert sm.is_expired("s1", 15) is False

def test_touch_boundary_alive():
    sm = SessionManager(10)
    sm.create("s1", 0)
    sm.touch("s1", 10)
    assert sm.is_expired("s1", 20) is False

def test_repeated_touch_stays_alive():
    sm = SessionManager(10)
    sm.create("s1", 0)
    sm.touch("s1", 8)
    sm.touch("s1", 16)
    assert sm.is_expired("s1", 25) is False
