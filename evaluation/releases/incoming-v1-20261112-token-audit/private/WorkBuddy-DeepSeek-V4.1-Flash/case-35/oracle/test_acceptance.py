from target import *


def test_acceptance_1():
    assert merge_config({'t': {'size': 2, 'dark': True}}, {'t': {'size': 3}}) == {'t': {'size': 3, 'dark': True}}


def test_acceptance_2():
    assert merge_config({'t': {'a': 1}}, {'t': {'b': 2}}, {'t': {'c': 3}}) == {'t': {'a': 1, 'b': 2, 'c': 3}}


def test_acceptance_3():
    assert merge_config({'a': {'b': {'c': 1, 'd': 2}}}, {'a': {'b': {'d': 3}}}) == {'a': {'b': {'c': 1, 'd': 3}}}
