"""Turnstile patcher: deterministic verify() based on token shape.

Convention:
    - "" or None        → False
    - "FAIL"            → False
    - "EXPIRED"         → False
    - anything else     → True (use ``OK-...`` for clarity in tests)
"""

from __future__ import annotations


def patch_turnstile(monkeypatch) -> None:
    """Replace ``app.services.turnstile.verify`` with a sync stub."""
    from app.services import turnstile

    async def _fake_verify(token: str | None, *, remote_ip: str | None = None) -> bool:
        if not token:
            return False
        if token in ("FAIL", "EXPIRED"):
            return False
        return True

    monkeypatch.setattr(turnstile, "verify", _fake_verify)
