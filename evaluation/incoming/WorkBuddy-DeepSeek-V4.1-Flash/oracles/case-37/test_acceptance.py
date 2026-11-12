from target import *

def _raises(fn):
    try:
        fn()
    except ValueError:
        return True
    return False

def _calls(limit):
    box = {'n': 0}
    def make():
        box['n'] += 1
        raise RuntimeError('boom')
    try:
        run_with_retry(make, limit)
    except RuntimeError:
        pass
    return box['n']

def _succeeds_on_second():
    box = {'n': 0}
    def make():
        box['n'] += 1
        if box['n'] == 1:
            raise RuntimeError('boom')
        return 'ok'
    return run_with_retry(make, 3)


def test_acceptance_1():
    assert _calls(2) == 3


def test_acceptance_2():
    assert _calls(0) == 1


def test_acceptance_3():
    assert _calls(5) == 6
