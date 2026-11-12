from target import *

def _raises(fn):
    try:
        fn()
    except ValueError:
        return True
    return False


def test_existing_1():
    assert isinstance(parse_version('1.2.3'), tuple)


def test_existing_2():
    assert len(parse_version('1.2.3')) == 3


def test_existing_3():
    assert isinstance(parse_version('2'), tuple)
