from target import RoundRobin

def test_covers_all():
    rr = RoundRobin(["a", "b", "c"])
    got = [rr.next_server() for _ in range(3)]
    assert sorted(got) == ["a", "b", "c"]

def test_consecutive_differ():
    rr = RoundRobin(["a", "b"])
    assert rr.next_server() != rr.next_server()

def test_single_server():
    rr = RoundRobin(["only"])
    assert rr.next_server() == "only"
    assert rr.next_server() == "only"
