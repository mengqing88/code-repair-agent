from target import *

def _ttl_before():
    cache = TTLCache()
    cache.put('a', 'v', 100)
    return cache.get('a', 50)

def _ttl_missing():
    cache = TTLCache()
    return cache.get('nope', 0)

def _ttl_at_expiry():
    cache = TTLCache()
    cache.put('a', 'v', 100)
    return cache.get('a', 100)

def _ttl_after_expiry():
    cache = TTLCache()
    cache.put('a', 'v', 100)
    return cache.get('a', 101)

def _ttl_size_empty():
    return TTLCache().size()

def _ttl_size_after_expiry():
    cache = TTLCache()
    cache.put('a', 'v', 100)
    cache.get('a', 100)
    cache.put('b', 'w', 100)
    cache.get('b', 100)
    return cache.size()


def test_existing_1():
    assert _ttl_before() == 'v'


def test_existing_2():
    assert _ttl_missing() is None


def test_existing_3():
    assert _ttl_size_empty() == 0
