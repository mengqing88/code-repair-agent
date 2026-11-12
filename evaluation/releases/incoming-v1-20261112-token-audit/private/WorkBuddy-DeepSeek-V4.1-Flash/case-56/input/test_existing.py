from target import *

def _raises(fn):
    try:
        fn()
    except ValueError:
        return True
    return False

def _seq_one():
    return Sequence().next_id(1000)

def _seq_forward():
    seq = Sequence()
    return [seq.next_id(1000), seq.next_id(2000)]

def _seq_backward():
    seq = Sequence()
    seq.next_id(2000)
    return [seq.next_id(2000), seq.next_id(1500)]

def _seq_same_ms():
    seq = Sequence()
    return [seq.next_id(500), seq.next_id(500), seq.next_id(500)]

def _seq_unique_under_rollback():
    seq = Sequence()
    values = [seq.next_id(1000), seq.next_id(900), seq.next_id(950)]
    return len(values) == len(set(values)) and values == sorted(values)

def _seq_bad():
    return Sequence().next_id(-1)


def test_existing_1():
    assert _seq_one() == 1000


def test_existing_2():
    assert _seq_forward() == [1000, 2000]


def test_existing_3():
    assert _raises(lambda: _seq_bad())
