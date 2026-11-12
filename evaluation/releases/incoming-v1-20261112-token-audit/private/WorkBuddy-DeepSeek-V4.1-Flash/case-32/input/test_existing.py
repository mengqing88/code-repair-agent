from target import *


def test_existing_1():
    assert billable_hours(540, 660) == 2


def test_existing_2():
    assert billable_hours(480, 1020) == 9


def test_existing_3():
    assert billable_hours(1320, 1380) == 1
