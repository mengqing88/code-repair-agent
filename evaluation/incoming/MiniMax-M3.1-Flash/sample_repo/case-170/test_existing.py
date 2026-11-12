import pytest

from target import TokenBucket


def test_invalid_rate_raises():
    with pytest.raises(ValueError):
        TokenBucket(rate=0, capacity=1)


def test_invalid_capacity_raises():
    with pytest.raises(ValueError):
        TokenBucket(rate=1, capacity=0)


def test_first_request_succeeds():
    bucket = TokenBucket(rate=1, capacity=1)
    assert bucket.acquire(1, now=0.0) is True
