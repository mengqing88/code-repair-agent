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


def test_existing_1():
    assert run_with_retry(lambda: 'ok', 1) == 'ok'


def test_existing_2():
    assert run_with_retry(lambda: 'ok', 5) == 'ok'


def test_existing_3():
    assert _succeeds_on_second() == 'ok'
