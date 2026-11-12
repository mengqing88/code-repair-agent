from target import *

def _raises(fn):
    try:
        fn()
    except ValueError:
        return True
    return False

import decimal


def test_existing_1():
    assert round_half_up('1.234') == decimal.Decimal('1.23')


def test_existing_2():
    assert round_half_up('0') == decimal.Decimal('0.00')


def test_existing_3():
    assert _raises(lambda: round_half_up('1.5', places=-1))
