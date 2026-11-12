from target import *


def test_acceptance_1():
    assert Policy([('admin', False), ('admin', True)]).allowed('admin') is False


def test_acceptance_2():
    assert Policy([('admin', True), ('admin', False)]).allowed('admin') is True


def test_acceptance_3():
    assert Policy([('editor', False), ('admin', True), ('editor', True)]).allowed('editor') is False
