"""Existing behaviour of the retry helper."""

import pytest

from retry import RetryError, retry_call


def test_a_successful_call_is_not_repeated():
    calls = []

    def work():
        calls.append(1)
        return "ok"

    assert retry_call(work, attempts=3) == "ok"
    assert len(calls) == 1


def test_zero_attempts_is_rejected():
    with pytest.raises(ValueError):
        retry_call(lambda: None, attempts=0)


def test_an_exhausted_retry_raises_retry_error():
    def work():
        raise RuntimeError("boom")

    with pytest.raises(RetryError):
        retry_call(work, attempts=1)
