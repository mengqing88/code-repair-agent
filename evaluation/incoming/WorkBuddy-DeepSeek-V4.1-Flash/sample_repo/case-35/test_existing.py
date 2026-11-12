from target import *


def test_existing_1():
    assert merge_config({'a': 1}, {'b': 2}) == {'a': 1, 'b': 2}


def test_existing_2():
    assert merge_config() == {}


def test_existing_3():
    assert merge_config(None, {'a': 1}) == {'a': 1}
