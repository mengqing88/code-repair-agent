from target import *


def test_acceptance_1():
    assert discounted(200) == 0.08


def test_acceptance_2():
    assert discounted(100) == 0.05


def test_acceptance_3():
    assert discounted(500) == 0.12
