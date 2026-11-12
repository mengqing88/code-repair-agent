import pytest

from target import TokenBucket


def test_fractional_second_refills_fractional_tokens():
    bucket = TokenBucket(rate=2, capacity=2)
    assert bucket.acquire(2, now=0.0) is True
    assert bucket.acquire(1, now=0.5) is True


def test_fractional_second_accumulates_on_top_of_remaining_tokens():
    bucket = TokenBucket(rate=2, capacity=2)
    assert bucket.acquire(1, now=0.0) is True
    assert bucket.acquire(2, now=0.5) is True


def test_sub_second_refill_at_high_rate_produces_enough_tokens():
    bucket = TokenBucket(rate=4, capacity=4)
    assert bucket.acquire(4, now=0.0) is True
    assert bucket.acquire(3, now=0.75) is True
