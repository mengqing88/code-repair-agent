"""Existing behaviour of the single flight loader."""

import pytest

from singleflight import SingleFlight


def test_a_successful_load_is_cached():
    flight = SingleFlight()
    calls = []

    def loader():
        calls.append(1)
        return "value"

    assert flight.run("k", loader) == "value"
    assert flight.run("k", loader) == "value"
    assert len(calls) == 1


def test_a_re_entrant_load_is_rejected():
    flight = SingleFlight()

    def loader():
        return flight.run("k", lambda: "inner")

    with pytest.raises(RuntimeError):
        flight.run("k", loader)


def test_an_idle_key_is_not_running():
    assert SingleFlight().is_running("k") is False
