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


def test_acceptance_1():
    assert _seq_backward() == [2001, 2002]


def test_acceptance_2():
    assert _seq_same_ms() == [500, 501, 502]


def test_acceptance_3():
    assert _seq_unique_under_rollback() is True
