from target import ReleaseManager

def test_first_release():
    rm = ReleaseManager()
    rm.add("b1", 5)
    rm.add("b2", 9)
    assert rm.release_expired(10) == ["b1", "b2"]

def test_future_kept():
    rm = ReleaseManager()
    rm.add("b1", 5)
    rm.add("b2", 20)
    assert rm.release_expired(10) == ["b1"]

def test_boundary_released():
    rm = ReleaseManager()
    rm.add("b1", 10)
    assert rm.release_expired(10) == ["b1"]
