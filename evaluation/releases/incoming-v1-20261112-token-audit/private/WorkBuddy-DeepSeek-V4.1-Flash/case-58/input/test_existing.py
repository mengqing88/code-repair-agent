from target import *


def test_existing_1():
    assert Policy([]).allowed('admin') is False


def test_existing_2():
    assert Policy([('admin', True)]).allowed('admin') is True


def test_existing_3():
    assert Policy([('guest', True)]).allowed('admin') is False
