from target import IdempotencyStore

def make_counting_fn():
    calls = []
    def fn():
        calls.append(1)
        return len(calls)
    return fn, calls

def test_fn_called_once():
    s = IdempotencyStore()
    fn, calls = make_counting_fn()
    s.run("k", fn)
    s.run("k", fn)
    assert len(calls) == 1

def test_three_calls_one_execution():
    s = IdempotencyStore()
    fn, calls = make_counting_fn()
    for _ in range(3):
        s.run("k", fn)
    assert len(calls) == 1

def test_same_object_returned():
    s = IdempotencyStore()
    first = s.run("k", lambda: [])
    second = s.run("k", lambda: [])
    assert first is second
