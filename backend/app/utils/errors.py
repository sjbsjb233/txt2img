"""Uniform 4xx/5xx response shaping.

Every error handed to FastAPI through ``api_error()`` lands as the JSON
shape declared in design doc §17::

    {
      "detail": {
        "code": "HARD_QUOTA_EXCEEDED",
        "message": "Daily limit reached. ...",
        "field": null,
        "extra": null
      }
    }

The only reason this exists is to keep error codes consistent across
modules. ``code`` is the load-bearing field for clients — frontends
branch on it for i18n and routing decisions; ``message`` is a fallback
human-readable string.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException


def api_error(
    status_code: int,
    code: str,
    message: str,
    *,
    field: str | None = None,
    extra: dict[str, Any] | None = None,
) -> HTTPException:
    """Build an ``HTTPException`` whose ``detail`` matches the §17 shape."""
    return HTTPException(
        status_code=status_code,
        detail={
            "code": code,
            "message": message,
            "field": field,
            "extra": extra,
        },
    )
