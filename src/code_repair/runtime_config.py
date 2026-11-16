"""Task-local frozen settings; credentials are resolved by name, never persisted."""
from contextlib import contextmanager
from contextvars import ContextVar
import os
from pathlib import Path

from code_repair.paths import PROJECT_ROOT

from dotenv import dotenv_values


_manifest = ContextVar("execution_manifest", default=None)


def active_manifest():
    return _manifest.get()


@contextmanager
def configuration_scope(manifest):
    token = _manifest.set(manifest)
    try:
        yield
    finally:
        _manifest.reset(token)


def load_settings(env_path=None):
    manifest = active_manifest()
    if manifest is None:
        return dict(dotenv_values(env_path or (PROJECT_ROOT / ".env"),
                                  encoding="utf-8-sig", interpolate=False))
    settings = dict(manifest["settings"]["values"])
    # A credential may rotate, but neither its selected reference nor its endpoint changes.
    local = dotenv_values(env_path or (PROJECT_ROOT / ".env"),
                          encoding="utf-8-sig", interpolate=False)
    for name, reference in manifest["settings"]["credentials"].items():
        settings[name] = ((os.getenv(reference["ref"]) or local.get(reference["ref"]) or "")
                          if reference["present"] else "")
    return settings


def setting(name, settings=None, default=None):
    if active_manifest() is not None:
        values = settings if settings is not None else load_settings()
        return values.get(name, default)
    return os.getenv(name) or (settings or {}).get(name) or default
