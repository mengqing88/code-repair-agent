from target import RoundRobin

def test_full_cycle_sequence():
    rr = RoundRobin(["a", "b", "c"])
    got = [rr.next_server() for _ in range(6)]
    assert got == ["a", "b", "c", "a", "b", "c"]

def test_first_call_is_first_server():
    rr = RoundRobin(["a", "b", "c"])
    assert rr.next_server() == "a"

def test_two_servers_sequence():
    rr = RoundRobin(["a", "b"])
    got = [rr.next_server() for _ in range(4)]
    assert got == ["a", "b", "a", "b"]
