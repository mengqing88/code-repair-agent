from target import *


def test_existing_1():
    assert group_by_warehouse([]) == {}


def test_existing_2():
    assert group_by_warehouse([{'warehouse': 'A', 'sku': 'x'}]) == {'A': ['x']}


def test_existing_3():
    assert list(group_by_warehouse([{'warehouse': 'A', 'sku': 'x'}, {'warehouse': 'B', 'sku': 'y'}])) == ['A', 'B']
