from target import *


def test_existing_1():
    assert discounted(0) == 0.0


def test_existing_2():
    assert discounted(150) == 0.05


def test_existing_3():
    assert discounted(600) == 0.12
