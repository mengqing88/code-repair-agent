"""Existing behaviour of the payload validation."""

from validation import validate


def test_a_valid_payload_has_no_errors():
    assert validate({"active": True, "age": 30, "name": "ana"}) == []


def test_a_wrong_type_is_reported():
    assert validate({"active": True, "age": "30", "name": "ana"}) == ["field age must be int"]


def test_every_bad_field_is_reported():
    errors = validate({"active": "yes", "age": 30, "name": "ana"})
    assert errors == ["field active must be bool"]
