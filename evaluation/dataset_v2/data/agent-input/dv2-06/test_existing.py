"""Existing behaviour of the connection pool."""

import pytest

from pool import Pool, PoolError


def test_a_lease_returns_the_connection():
    pool = Pool(1)
    with pool.lease() as conn:
        assert conn.ident == 0
    assert pool.available() == 1


def test_an_exhausted_pool_rejects_a_new_lease():
    pool = Pool(1)
    with pool.lease():
        with pytest.raises(PoolError):
            pool.acquire()


def test_a_closed_pool_rejects_new_connections():
    pool = Pool(1)
    pool.close()
    with pytest.raises(PoolError):
        pool.acquire()
