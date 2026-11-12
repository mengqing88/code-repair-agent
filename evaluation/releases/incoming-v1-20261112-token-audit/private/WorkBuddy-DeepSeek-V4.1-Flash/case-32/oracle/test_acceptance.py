from target import *


def test_acceptance_1():
    assert billable_hours(1320, 61) == 4


def test_acceptance_2():
    assert billable_hours(600, 601) == 1


def test_acceptance_3():
    assert billable_hours(120, 120) == 24
