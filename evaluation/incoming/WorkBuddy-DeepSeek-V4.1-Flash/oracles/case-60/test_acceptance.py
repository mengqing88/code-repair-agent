from target import *

def _raises(fn):
    try:
        fn()
    except ValueError:
        return True
    return False

def _inv_reserve_ok():
    inv = Inventory(10)
    return [inv.reserve(3), inv.available()]

def _inv_zero():
    inv = Inventory(2)
    return [inv.reserve(2), inv.available()]

def _inv_over():
    inv = Inventory(2)
    return [inv.reserve(5), inv.available()]

def _inv_over_large():
    inv = Inventory(5)
    return [inv.reserve(50), inv.available()]

def _inv_exact():
    inv = Inventory(3)
    return [inv.reserve(3), inv.available()]

def _inv_repeat():
    inv = Inventory(2)
    first = inv.reserve(3)
    second = inv.reserve(3)
    return [first, second]

def _inv_bad():
    Inventory(-1)


def test_acceptance_1():
    assert _inv_over() == [False, 2]


def test_acceptance_2():
    assert _inv_over_large() == [False, 5]


def test_acceptance_3():
    assert _inv_repeat() == [False, False]
