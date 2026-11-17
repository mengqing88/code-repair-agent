"""URL building for the public API client."""

from __future__ import annotations

from urllib.parse import urlencode


def build_url(base, path, params=None, *, trailing_slash=False):
    """Join ``base`` and ``path`` and append the query string."""
    url = base.rstrip("/") + "/" + path.lstrip("/")
    if trailing_slash and not url.endswith("/"):
        url += "/"
    if params:
        clean = {key: value for key, value in params.items() if value}
        if clean:
            url += "?" + urlencode(clean, doseq=True)
    return url
