from target import *

def _raises(fn):
    try:
        fn()
    except ValueError:
        return True
    return False

def _alpha(arg):
    if isinstance(arg, list):
        return '[' + str(arg[0]) + ']'
    return str(arg).upper()

def _memo_first():
    memo = Memoizer(_alpha)
    return memo.get('a')

def _memo_calls():
    box = {'n': 0}
    def func(arg):
        box['n'] += 1
        return str(arg).upper()
    memo = Memoizer(func)
    memo.get('a')
    memo.get('a')
    return box['n']

def _memo_repeat():
    memo = Memoizer(_alpha)
    memo.get('a')
    return memo.get('a')

def _memo_size():
    memo = Memoizer(_alpha)
    memo.get('a')
    memo.get('a')
    return memo.cache_size()

def _memo_two_keys():
    memo = Memoizer(_alpha)
    return [memo.get('x'), memo.get('y')]

def _memo_size_two_keys():
    memo = Memoizer(_alpha)
    memo.get('x')
    memo.get('y')
    return memo.cache_size()

def _memo_calls_two_keys():
    box = {'n': 0}
    def func(arg):
        box['n'] += 1
        return str(arg).upper()
    memo = Memoizer(func)
    memo.get('x')
    memo.get('y')
    return box['n']

def _memo_unhashable():
    memo = Memoizer(_alpha)
    return [memo.get([1]), memo.get([1])]

def _memo_three_keys():
    memo = Memoizer(_alpha)
    return [memo.get('x'), memo.get('y'), memo.get('z')]

def _memo_calls_for(key):
    box = {'n': 0}
    def func(arg):
        box['n'] += 1
        return str(arg).upper()
    memo = Memoizer(func)
    memo.get(key)
    memo.get(key)
    memo.get(key)
    return box['n']


def test_existing_1():
    assert _memo_first() == 'A'


def test_existing_2():
    assert _memo_repeat() == 'A'


def test_existing_3():
    assert _memo_size() == 1
