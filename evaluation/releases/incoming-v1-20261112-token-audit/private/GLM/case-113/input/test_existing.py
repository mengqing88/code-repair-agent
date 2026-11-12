from target import SessionManager

def test_fresh_session_alive():
    sm = SessionManager(10)
    sm.create("s1", 0)
    assert sm.is_expired("s1", 5) is False

def test_untouched_expires():
    sm = SessionManager(10)
    sm.create("s1", 0)
    assert sm.is_expired("s1", 11) is True

def test_two_sessions_independent():
    sm = SessionManager(10)
    sm.create("a", 0)
    sm.create("b", 5)
    assert sm.is_expired("a", 11) is True
    assert sm.is_expired("b", 11) is False
