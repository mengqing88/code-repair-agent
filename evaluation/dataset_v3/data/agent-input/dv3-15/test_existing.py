"""Existing behaviour of the upstream wrapper."""

import pytest

from wrapper import UpstreamError, call_upstream


def test_a_successful_call_is_passed_through():
    assert call_upstream(lambda: "ok") == "ok"


def test_a_timeout_becomes_an_upstream_error():
    def boom():
        raise TimeoutError("slow")

    with pytest.raises(UpstreamError):
        call_upstream(boom)


def test_an_invalid_response_becomes_an_upstream_error():
    def bad():
        raise ValueError("missing field")

    with pytest.raises(UpstreamError):
        call_upstream(bad)
