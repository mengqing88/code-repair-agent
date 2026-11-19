"""A tiny undo/redo editor."""

from __future__ import annotations


class StateError(RuntimeError):
    """Raised when an undo or redo is not possible."""


class Editor:
    def __init__(self, text=""):
        self.text = text
        self._undo = []
        self._redo = []

    def type(self, addition):
        self._undo.append(self.text)
        self.text += addition

    def undo(self):
        if not self._undo:
            raise StateError("nothing to undo")
        self._redo.append(self.text)
        self.text = self._undo.pop()

    def redo(self):
        if not self._redo:
            raise StateError("nothing to redo")
        self._undo.append(self.text)
        self.text = self._redo.pop()
