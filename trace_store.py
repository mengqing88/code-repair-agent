"""Trace lineage, redacted payloads and append-only JSONL pagination."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import math
import os
from pathlib import Path
from uuid import uuid4

from diagnostics import redact, sensitive_name


_span = ContextVar("repair_trace_span", default={})


def trace_context():
    return _span.get()


@contextmanager
def trace_scope(call_id=None, **labels):
    previous = trace_context()
    values = {**previous, **labels, "call_id": call_id or uuid4().hex,
              "parent_call_id": previous.get("call_id")}
    token = _span.set(values)
    try:
        yield values["call_id"]
    finally:
        _span.reset(token)


def safe_trace_data(value):
    """Retain complete observed data while masking credentials, including in code/log strings."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            result[str(key)] = "[redacted]" if sensitive_name(key) else safe_trace_data(item)
        return result
    if isinstance(value, (list, tuple)):
        return [safe_trace_data(item) for item in value]
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    text = str(value)
    return redact(text, limit=len(text) + 1)


@contextmanager
def _write_lock(path):
    # A separate lock serializes worker/supervisor writes, including repair of a torn last line.
    with path.with_suffix(path.suffix + ".lock").open("a+b") as lock:
        if os.name == "nt":
            import msvcrt
            # Windows permits locking beyond EOF. Initializing the byte here races
            # with another process that has already locked it and can drop a record.
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def append_trace(path, record):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    with _write_lock(path), path.open("a+b") as stream:
        if stream.seek(0, 2):
            stream.seek(-1, 2)
            if stream.read(1) != b"\n":
                stream.write(b"\n")
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def read_trace_page(path, *, cursor=0, limit=200):
    """Byte cursors allow bounded reads of old/new records and polling a growing file."""
    events, skipped, position, more = [], 0, cursor, False
    path = Path(path)
    if not path.exists():
        if cursor:
            raise ValueError("Trace 游标超出文件范围")
        return {"events": [], "next_cursor": 0, "has_more": False, "skipped_invalid_records": 0}
    with path.open("rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        if cursor > size:
            raise ValueError("Trace 游标超出文件范围")
        if cursor:
            stream.seek(cursor - 1)
            if stream.read(1) != b"\n":
                raise ValueError("Trace 游标必须位于记录边界")
        stream.seek(cursor)
        while len(events) < limit and stream.tell() < size:
            start = stream.tell()
            line = stream.readline(size - start)
            if not line.endswith(b"\n"):
                # An active writer may complete this record later. Never consume it prematurely.
                skipped += 1
                position = start
                break
            position = stream.tell()
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError("Trace 记录必须是对象")
                events.append(record)
            except (ValueError, UnicodeDecodeError):
                skipped += 1
        else:
            more = position < size
    return {"events": events, "next_cursor": position, "has_more": more,
            "skipped_invalid_records": skipped}
