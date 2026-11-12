from target import *


def test_existing_1():
    assert split_amounts([('expense', 100)])['expense'] == 100


def test_existing_2():
    assert split_amounts([]) == {'income': 0, 'expense': 0, 'refund': 0}


def test_existing_3():
    assert split_amounts([('expense', 30)])['expense'] == 30
