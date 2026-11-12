from target import *


def test_acceptance_1():
    assert split_amounts([('refund', 30)]) == {'income': 0, 'expense': 0, 'refund': 30}


def test_acceptance_2():
    assert split_amounts([('income', -100)]) == {'income': -100, 'expense': 0, 'refund': 0}


def test_acceptance_3():
    assert split_amounts([('refund', 30), ('income', -100)]) == {'income': -100, 'expense': 0, 'refund': 30}
