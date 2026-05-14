"""Per-user captcha grace window.

After a successful Turnstile verification, the user enters a short grace
window during which :func:`_recent_burst` is bypassed for them. This lets
one captcha cover an entire fan-out (or any other multi-step submission
burst) instead of demanding N captchas in a row when N parallel
sub-requests all trip the rolling burst threshold.

Single-node, in-process TTL dict. Multi-node deploys that observe lost
grace across pods should swap the backing store for Redis behind the
same ``mark`` / ``is_in_grace`` interface.

Window duration matches :func:`app.api.jobs._recent_burst`'s own 60 s
window so a captcha covers exactly the same anti-abuse epoch that
minted it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


_GRACE_SECONDS = 60


@dataclass
class _Entry:
    expires_at: datetime


class CaptchaGrace:
    """In-process per-user TTL store."""

    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}
        self._lock = asyncio.Lock()

    async def mark(self, user_id: str) -> None:
        """Open a 60 s grace window for ``user_id`` starting now."""
        expires = datetime.now(timezone.utc) + timedelta(seconds=_GRACE_SECONDS)
        async with self._lock:
            self._entries[user_id] = _Entry(expires_at=expires)

    async def is_in_grace(self, user_id: str) -> bool:
        """True iff the user has an unexpired grace window."""
        async with self._lock:
            entry = self._entries.get(user_id)
            if entry is None:
                return False
            if entry.expires_at <= datetime.now(timezone.utc):
                # Lazy eviction — keeps the dict from growing unbounded
                # without a sweeper goroutine. Active-user count stays
                # bounded naturally.
                self._entries.pop(user_id, None)
                return False
            return True


_instance: CaptchaGrace | None = None


def get_captcha_grace() -> CaptchaGrace:
    global _instance
    if _instance is None:
        _instance = CaptchaGrace()
    return _instance


def reset_captcha_grace_for_tests() -> None:
    global _instance
    _instance = None
