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
# Cap on the number of stored entries before we proactively sweep the
# whole dict. A user who solves Turnstile and never comes back leaves
# one entry behind; lazy ``is_in_grace`` eviction only fires when that
# same user is read again, so without a hard cap the dict can drift up
# slowly under churn. 50k entries × ~150 B/entry ≈ 7 MB worst case,
# well inside what a single backend pod can carry.
_HARD_CAP_ENTRIES = 50_000


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
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=_GRACE_SECONDS)
        async with self._lock:
            # Opportunistic sweep on write: cheap amortised cost, and
            # the write path is where new entries arrive, so every
            # ``mark`` gets a chance to garbage-collect entries that
            # the read path never revisited (e.g. user solved a
            # captcha then walked away).
            self._sweep_locked(now)
            self._entries[user_id] = _Entry(expires_at=expires)

    async def is_in_grace(self, user_id: str) -> bool:
        """True iff the user has an unexpired grace window."""
        async with self._lock:
            entry = self._entries.get(user_id)
            if entry is None:
                return False
            if entry.expires_at <= datetime.now(timezone.utc):
                # Lazy eviction on read complements the ``mark`` sweep
                # so we never report ``True`` for a stale entry.
                self._entries.pop(user_id, None)
                return False
            return True

    def _sweep_locked(self, now: datetime) -> None:
        """Drop expired entries; must be called with ``self._lock`` held.

        Two cleanup tactics:
          1. Always strip any entry whose ``expires_at`` is in the past
             — that's the cheap, correctness-driven part.
          2. If we're still above the hard cap after #1 (which would
             only happen if a flood of in-window entries piled up
             faster than they expire), trim the oldest by expiry so
             memory stays bounded under adversarial conditions.
        """
        expired = [uid for uid, e in self._entries.items() if e.expires_at <= now]
        for uid in expired:
            self._entries.pop(uid, None)
        if len(self._entries) <= _HARD_CAP_ENTRIES:
            return
        overflow = len(self._entries) - _HARD_CAP_ENTRIES
        # Oldest expiry first — those will expire soonest anyway, so
        # dropping them costs us the least useful information.
        ordered = sorted(self._entries.items(), key=lambda kv: kv[1].expires_at)
        for uid, _ in ordered[:overflow]:
            self._entries.pop(uid, None)


_instance: CaptchaGrace | None = None


def get_captcha_grace() -> CaptchaGrace:
    global _instance
    if _instance is None:
        _instance = CaptchaGrace()
    return _instance


def reset_captcha_grace_for_tests() -> None:
    global _instance
    _instance = None
