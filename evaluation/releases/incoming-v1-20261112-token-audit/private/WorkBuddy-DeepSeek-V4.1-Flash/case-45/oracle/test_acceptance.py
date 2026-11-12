from target import *


def test_acceptance_1():
    assert [(x['sales'], x['price']) for x in sort_items([{'sales': 1, 'price': 9}, {'sales': 1, 'price': 5}, {'sales': 0, 'price': 1}])] == [(0, 1), (1, 9), (1, 5)]


def test_acceptance_2():
    assert [x['sales'] for x in sort_items([{'sales': 5, 'price': 1}, {'sales': 2, 'price': 1}])] == [2, 5]


def test_acceptance_3():
    assert [x['sales'] for x in sort_items([{'sales': 3, 'price': 1}, {'sales': 9, 'price': 9}, {'sales': 1, 'price': 9}])] == [1, 3, 9]
