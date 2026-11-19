"""Existing behaviour of the template renderer."""

from render import render


def test_a_single_placeholder_is_replaced():
    assert render("hi {{name}}", {"name": "ana"}) == "hi ana"


def test_text_without_placeholders_is_unchanged():
    assert render("plain text", {}) == "plain text"


def test_numbers_are_stringified():
    assert render("{{n}} items", {"n": 3}) == "3 items"
