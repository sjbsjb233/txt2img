"""Redaction helpers for upstream payloads.

Anything that ends up on disk under ``data/jobs/<hash>/upstream/`` or in
the response of ``/api/admin/jobs/<hash>/inspect`` flows through here
first. Two layers — the disk layer caps at ~500 chars, the API layer
caps at 200 chars. We always strip credentials regardless of length.
"""

from __future__ import annotations

import json
import re
from typing import Any


_BEARER_RE = re.compile(
    r"(Authorization\s*:\s*\S+|Bearer\s+[A-Za-z0-9._\-+/=]{16,}|sk-[A-Za-z0-9_\-]{16,})",
    re.IGNORECASE,
)

_SENSITIVE_JSON_KEYS = {
    "api_key",
    "apikey",
    "key",
    "token",
    "access_token",
    "refresh_token",
    "password",
    "secret",
    "authorization",
}


def _redact_strings(value: Any) -> Any:
    if isinstance(value, str):
        return _BEARER_RE.sub("[REDACTED]", value)
    if isinstance(value, list):
        return [_redact_strings(v) for v in value]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if isinstance(k, str) and k.lower() in _SENSITIVE_JSON_KEYS:
                out[k] = "[REDACTED]"
            else:
                out[k] = _redact_strings(v)
        return out
    return value


def redact_payload(value: Any) -> Any:
    """Walk a JSON-shaped value, scrub credentials. Pure / no I/O."""
    return _redact_strings(value)


def redact_upstream_body(text: str | None, max_len: int = 500) -> str | None:
    """Scrub a free-form text body and clip it to ``max_len``.

    The text may be a JSON blob, a stack trace, or a raw HTTP response.
    We try to JSON-load first so structured fields (``api_key`` etc.)
    can be removed by key; on parse failure we fall back to regex
    redaction over the raw string.
    """
    if text is None:
        return None
    if not isinstance(text, str):
        text = str(text)

    # Try structured redaction first.
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        parsed = None

    if parsed is not None:
        cleaned = redact_payload(parsed)
        text = json.dumps(cleaned, ensure_ascii=False, separators=(",", ":"))
    else:
        text = _BEARER_RE.sub("[REDACTED]", text)

    if max_len and len(text) > max_len:
        text = text[: max_len].rstrip() + "..."
    return text


def excerpt_for_response(text: str | None, max_len: int = 200) -> str | None:
    """Already-redacted text → short excerpt suitable for the API."""
    if text is None:
        return None
    if len(text) > max_len:
        return text[:max_len].rstrip() + "..."
    return text


__all__ = (
    "redact_payload",
    "redact_upstream_body",
    "excerpt_for_response",
)
