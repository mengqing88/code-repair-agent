from target import *


def test_existing_1():
    assert sort_items([]) == []


def test_existing_2():
    assert sort_items([{'sales': 1, 'price': 1}]) == [{'sales': 1, 'price': 1}]


def test_existing_3():
    assert [x['sales'] for x in sort_items([{'sales': 3, 'price': 1}, {'sales': 1, 'price': 9}])] == [1, 3]
