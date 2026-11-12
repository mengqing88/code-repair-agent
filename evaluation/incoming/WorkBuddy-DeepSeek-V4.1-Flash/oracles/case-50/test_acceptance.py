from target import *

def _raises(fn):
    try:
        fn()
    except ValueError:
        return True
    return False


def test_acceptance_1():
    assert parse_version('1.10.0') > parse_version('1.9.0')


def test_acceptance_2():
    assert parse_version('1.0') == parse_version('1.0.0')


def test_acceptance_3():
    assert _raises(lambda: parse_version('1.x'))
