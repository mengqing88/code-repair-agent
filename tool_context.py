"""Request-local identity for one model-issued tool call."""
from contextlib import contextmanager
from contextvars import ContextVar


_call_id = ContextVar("repair_model_tool_call", default=None)


@contextmanager
def tool_call_scope(call_id):
    token = _call_id.set(call_id)
    try:
        yield
    finally:
        _call_id.reset(token)


def current_tool_call_id():
    return _call_id.get()
