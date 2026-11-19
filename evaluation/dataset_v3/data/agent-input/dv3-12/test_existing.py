"""Existing behaviour of the editor."""

import pytest

from editor import Editor, StateError


def test_typing_then_undoing_restores_the_text():
    editor = Editor()
    editor.type("ab")
    editor.undo()
    assert editor.text == ""


def test_redo_reapplies_the_undone_step():
    editor = Editor()
    editor.type("ab")
    editor.undo()
    editor.redo()
    assert editor.text == "ab"


def test_undo_on_empty_history_is_rejected():
    with pytest.raises(StateError):
        Editor().undo()
