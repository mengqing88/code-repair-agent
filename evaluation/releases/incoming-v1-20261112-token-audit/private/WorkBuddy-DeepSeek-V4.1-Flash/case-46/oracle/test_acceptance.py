from target import *


def test_acceptance_1():
    assert group_by_warehouse([{'warehouse': 'A', 'sku': 'x'}, {'warehouse': 'A', 'sku': 'y'}]) == {'A': ['x', 'y']}


def test_acceptance_2():
    assert group_by_warehouse([{'warehouse': 'A', 'sku': 'x'}, {'warehouse': 'A', 'sku': 'y'}, {'warehouse': 'B', 'sku': 'z'}, {'warehouse': 'A', 'sku': 'w'}]) == {'A': ['x', 'y', 'w'], 'B': ['z']}


def test_acceptance_3():
    assert group_by_warehouse([{'warehouse': 'A', 'sku': 'x'}, {'warehouse': 'A', 'sku': 'y'}])['A'][0] == 'x'
