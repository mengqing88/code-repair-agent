"""Bounded, redacted diagnostics with request/task correlation and independent fallback sinks."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import traceback
from uuid import uuid4


_scope = ContextVar("repair_diagnostics", default={})


@contextmanager
def diagnostic_scope(*, path=None, task_id=None, request_id=None, operation=None):
    values = {**_scope.get(), **{key: value for key, value in {
        "path": path, "task_id": task_id, "request_id": request_id, "operation": operation,
    }.items() if value is not None}}
    token = _scope.set(values)
    try:
        yield values
    finally:
        _scope.reset(token)


def sensitive_name(name):
    """Recognize credential fields in headers, snake_case and camelCase without hiding usage counters."""
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(name))
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized.lower()).strip("_")
    if normalized in {"token_usage", "token_count", "token_budget", "token_limit"}:
        return False
    words = set(normalized.split("_"))
    return bool(words & {"password", "passwd", "pwd", "secret", "secrets", "token", "credential",
                         "credentials", "authorization", "cookie", "cookies"}
                or "private" in words and "key" in words
                or "api" in words and "key" in words
                or "apikey" in words or "session" in words and "id" in words
                or "database" in words and "url" in words or "db" in words and "url" in words)


def redact(value, limit=2000):
    from input_protection import protect_input
    text = protect_input(str(value))
    secrets = [value for name, value in os.environ.items()
               if any(part in name.upper() for part in ("API_KEY", "PASSWORD", "SECRET", "TOKEN", "DATABASE_URL", "KNOWLEDGE_DB_URL"))
               and len(value) >= 4]
    # Configuration files are also a source of credentials; never include their contents in diagnostics.
    try:
        from dotenv import dotenv_values
        settings = dotenv_values(Path(__file__).with_name(".env"), interpolate=False)
        secrets.extend(value for name, value in settings.items() if value and len(value) >= 4
                       and any(part in name.upper() for part in ("KEY", "PASSWORD", "SECRET", "TOKEN", "DB_URL", "DATABASE_URL")))
    except Exception:
        pass
    for secret in sorted(set(secrets), key=len, reverse=True):
        text = text.replace(secret, "[redacted]")
    text = re.sub(r"-----BEGIN (?:[A-Z0-9 ]* )?PRIVATE KEY-----.*?-----END (?:[A-Z0-9 ]* )?PRIVATE KEY-----",
                  "[redacted-private-key]", text, flags=re.DOTALL)
    text = re.sub(r"(?i)Bearer\s+[^\s\"']+", "Bearer [redacted]", text)
    text = re.sub(r"(?i)(https?|postgres(?:ql)?)://[^\s\"']+", "[redacted-url]", text)
    # Header values can contain spaces or several cookies; mask the whole line, including Basic auth.
    text = re.sub(r"(?im)(\b(?:proxy-authorization|authorization|set-cookie|cookie)\s*:\s*)[^\r\n]+",
                  r"\1[redacted]", text)
    credential_name = (r"(?=[\w.-]*(?:password|passwd|pwd|secret|token|credential|authorization|cookie|"
                       r"private[_-]?key|api[_-]?key|session[_-]?id|database[_-]?url|db[_-]?url))[A-Za-z_][\w.-]*")
    quoted = (r"(?<![\w.-])(?P<prefix>(?P<label_quote>[\"']?)(?P<name>" + credential_name + r")(?P=label_quote)\s*[:=]\s*)"
              r"(?P<quote>[\"'])(?:\\.|(?!(?P=quote)).)*(?P=quote)")
    text = re.sub(quoted, lambda match: match["prefix"] + match["quote"] + "[redacted]" + match["quote"]
                  if sensitive_name(match["name"]) else match[0], text, flags=re.IGNORECASE)
    text = re.sub(r"(?P<prefix>\b(?P<name>[A-Za-z_][\w.-]*)\s*[:=]\s*)[^\s,;\"']+",
                  lambda match: match["prefix"] + "[redacted]" if sensitive_name(match["name"]) else match[0], text)
    return text[:limit]


def exception_chain(error):
    result, seen = [], set()
    def visit(item):
        if item is None or id(item) in seen or len(result) >= 8:
            return
        seen.add(id(item))
        # Source lines and locals can contain credentials, prompts and code: record locations only.
        frames = [{"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
                  for frame in traceback.extract_tb(item.__traceback__)[-20:]]
        entry = {"type": type(item).__name__, "module": type(item).__module__, "frames": frames}
        for name in ("errno", "winerror", "returncode", "status_code", "sqlite_errorcode"):
            value = getattr(item, name, None)
            if isinstance(value, int):
                entry[name] = value
        result.append(entry)
        visit(item.__cause__ or (None if item.__suppress_context__ else item.__context__))
        for child in getattr(item, "exceptions", ())[:8]:
            visit(child)
    visit(error)
    return result


def process_evidence(stderr, return_code):
    """Retain crash locations and fingerprints, never arbitrary child stdout/stderr or source lines."""
    data = stderr if isinstance(stderr, bytes) else str(stderr or "").encode("utf-8", errors="replace")
    text = data[-32000:].decode("utf-8", errors="replace")
    frames = [{"file": Path(file).name, "line": int(line), "function": function}
              for file, line, function in re.findall(r'File "([^"]+)", line (\d+), in ([\w<>]+)', text)[-20:]]
    types = re.findall(r"^([\w.]+(?:Error|Exception|Exit|Interrupt))(?::|$)", text, re.MULTILINE)
    result = {"return_code": return_code, "stderr_bytes": len(data), "stderr_digest": hashlib.sha256(data).hexdigest(),
              "worker_exception_type": types[-1] if types else None, "worker_frames": frames}
    for line in reversed(text.splitlines()[-10:]):
        try:
            identifier = json.loads(line).get("error", {}).get("error_id", "")
            if isinstance(identifier, str) and re.fullmatch(r"[a-f0-9]{32}", identifier):
                result["worker_error_id"] = identifier
                break
        except (ValueError, AttributeError, TypeError):
            continue
    return result


def _append(path, record):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        stream.flush()


def record_error(error, info, *, operation=None, details=None):
    """Logging failure never replaces the original failure; all fallbacks are bounded and nonrecursive."""
    previous = getattr(error, "_repair_diagnostic", None)
    if previous:
        return dict(previous)
    scope = _scope.get()
    info = dict(info)
    info.setdefault("error_id", uuid4().hex)
    info["message"] = redact(info["message"])
    info["next_action"] = redact(info["next_action"])
    info["operation"] = operation or scope.get("operation", "unknown")
    info["request_id"] = scope.get("request_id")
    record = {"timestamp": datetime.now(timezone.utc).isoformat(), **info,
              "task_id": scope.get("task_id"), "exception_chain": exception_chain(error)}
    if details:
        # Callers provide structural evidence only; no source, arguments or model responses.
        record["details"] = details
    root = Path(os.environ.get("REPAIR_DATA_DIR", Path(__file__).parent / "work" / "web"))
    target = Path(scope.get("path", root / "errors.jsonl"))
    persisted = False
    try:
        _append(target, record)
        persisted = True
    except Exception as sink_error:
        fallback = {**record, "diagnostic_sink_error": {"type": type(sink_error).__name__, "errno": getattr(sink_error, "errno", None)}}
        try:
            _append(root / "errors-fallback.jsonl", fallback)
            persisted = True
        except Exception:
            try:
                sys.stderr.write(json.dumps(fallback, ensure_ascii=False, default=str) + "\n")
                sys.stderr.flush()
            except Exception:
                pass
    info["diagnostics_persisted"] = persisted
    try:
        error._repair_diagnostic = dict(info)
    except Exception:
        pass
    return info
