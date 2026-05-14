"""Unit tests for :mod:`app.domain.captcha_grace`.

API-level captcha-grace behaviour lives in ``test_jobs_api.py``; this
file owns the in-process TTL semantics — eviction, hard cap, and the
``mark`` / ``is_in_grace`` contract — so the store can be exercised
without booting the FastAPI lifespan for every assertion.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.domain import captcha_grace as cg_mod
from app.domain.captcha_grace import CaptchaGrace


@pytest.mark.asyncio
async def test_mark_then_is_in_grace_returns_true() -> None:
    grace = CaptchaGrace()
    await grace.mark("u_a")
    assert await grace.is_in_grace("u_a") is True


@pytest.mark.asyncio
async def test_unknown_user_is_not_in_grace() -> None:
    grace = CaptchaGrace()
    assert await grace.is_in_grace("u_never_saw_this") is False


@pytest.mark.asyncio
async def test_expired_entry_is_lazily_evicted_on_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``is_in_grace`` drops stale rows so the dict never reports True."""
    grace = CaptchaGrace()
    await grace.mark("u_b")

    # Fast-forward "now" past the 60 s window.
    real_now = cg_mod.datetime.now(timezone.utc) + timedelta(seconds=61)

    class _Clock:
        @staticmethod
        def now(tz=None):
            return real_now

    monkeypatch.setattr(cg_mod, "datetime", _Clock)
    assert await grace.is_in_grace("u_b") is False
    # Internal: row evicted, dict is empty.
    assert grace._entries == {}


@pytest.mark.asyncio
async def test_mark_sweeps_expired_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A write triggers a sweep of stale rows the read path never touched.

    Closes the leak Copilot flagged in PR #114: a user who solves
    Turnstile and never returns leaves one entry behind that lazy
    eviction would never see — sweeping on every ``mark`` ensures
    the dict eventually drains.
    """
    grace = CaptchaGrace()
    # Plant a row in the past so it's expired.
    past = datetime.now(timezone.utc) - timedelta(seconds=120)
    grace._entries["u_stale"] = cg_mod._Entry(expires_at=past)
    assert "u_stale" in grace._entries

    # Marking a different user should evict the stale one.
    await grace.mark("u_fresh")
    assert "u_stale" not in grace._entries
    assert "u_fresh" in grace._entries


@pytest.mark.asyncio
async def test_hard_cap_trims_oldest_when_overflowing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adversarial flood beyond ``_HARD_CAP_ENTRIES`` is bounded."""
    monkeypatch.setattr(cg_mod, "_HARD_CAP_ENTRIES", 8)

    grace = CaptchaGrace()
    now = datetime.now(timezone.utc)
    # 12 entries all in-window; oldest expiry first → those get
    # trimmed when we mark the 13th.
    for i in range(12):
        grace._entries[f"u_{i:02d}"] = cg_mod._Entry(
            expires_at=now + timedelta(seconds=10 + i)
        )

    await grace.mark("u_new")
    # 8 cap + 1 newcomer = 9 entries max after sweep.
    assert len(grace._entries) <= 9
    # The newest ones (highest expires_at) survive; the oldest are
    # the ones we drop.
    assert "u_00" not in grace._entries
    assert "u_new" in grace._entries


@pytest.mark.asyncio
async def test_concurrent_marks_are_safe() -> None:
    """``asyncio.Lock`` serialises writes; final state is well-defined."""
    grace = CaptchaGrace()
    await asyncio.gather(*(grace.mark(f"u_{i}") for i in range(50)))
    in_grace = await asyncio.gather(
        *(grace.is_in_grace(f"u_{i}") for i in range(50))
    )
    assert all(in_grace)
