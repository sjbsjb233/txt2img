"""Shared helpers for the batch test files (testing doc v0.3 §1.1).

Keeps the per-file boilerplate small: each test file imports just what
it needs (login, register, drive Job rows directly, capture SSE events).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select, update


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


async def login_admin(client: httpx.AsyncClient) -> str:
    resp = await client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-admin-password"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def login_user(
    client: httpx.AsyncClient,
    *,
    username: str = "alice",
    tier: str = "premium",
) -> str:
    """Insert a user via the ORM, return a fresh JWT for them."""
    from app.db.engine import get_session
    from app.db.models import User
    from app.utils.security import hash_password

    async with get_session() as session:
        existing = (
            await session.execute(select(User).where(User.username == username))
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                User(
                    id=f"u_test_{username}",
                    username=username,
                    password_hash=hash_password("alicepw1234"),
                    role="user",
                    tier=tier,
                )
            )

    resp = await client.post(
        "/api/auth/login",
        json={"username": username, "password": "alicepw1234"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Spec / register helpers
# ---------------------------------------------------------------------------


def slot_dict(
    *,
    stable_idx: int = 1,
    image_count: int = 1,
    set_id: str | None = None,
    session_id: str | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    return {
        "stable_idx": stable_idx,
        "title": title or f"slot {stable_idx}",
        "prompt_summary": "p",
        "image_count": image_count,
        "set_id": set_id,
        "session_id": session_id,
    }


def spec_dict(slots: list[dict[str, Any]], **overrides: Any) -> dict[str, Any]:
    base = {
        "fixed_prompt_summary": "fixed",
        "fixed_ref_count": 0,
        "session_strategy": "none",
        "shared_session_id": None,
        "slots": slots,
    }
    base.update(overrides)
    return base


def create_body(
    slots: list[dict[str, Any]] | None = None,
    *,
    title: str = "t",
    total_job_count: int | None = None,
    spec_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if slots is None:
        slots = [slot_dict(stable_idx=1, image_count=1)]
    spec = spec_dict(slots, **(spec_overrides or {}))
    if total_job_count is None:
        total_job_count = sum(s["image_count"] for s in slots)
    return {
        "title": title,
        "total_job_count": total_job_count,
        "spec": spec,
    }


async def register_batch(
    client: httpx.AsyncClient,
    token: str,
    *,
    title: str = "t",
    slots: list[dict[str, Any]] | None = None,
    spec_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = create_body(
        slots=slots, title=title, spec_overrides=spec_overrides
    )
    resp = await client.post("/api/batches", headers=auth(token), json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Direct Job manipulation (skip the executor)
# ---------------------------------------------------------------------------


async def insert_fake_job(
    *,
    user_id: str,
    batch_id: str | None,
    set_id: str | None = None,
    status: str = "QUEUED",
) -> str:
    """Insert a Job row directly. Returns the hash_id.

    Bypasses the executor + provider plumbing — tests that exercise the
    batch counters / state machine don't need a real model behind them.
    """
    from app.db.engine import get_session
    from app.db.jobs_repository import get_jobs_repository, serialise_params

    repo = get_jobs_repository()
    async with get_session() as session:
        created = await repo.insert_queued(
            user_id=user_id,
            tier_at_submit="premium",
            model="gpt-image-2",
            params_json=serialise_params({"model": "gpt-image-2", "prompt": "p"}),
            client_request_id=None,
            set_id=set_id,
            session_id=None,
            batch_id=batch_id,
            session=session,
        )
    if status != "QUEUED":
        await force_job_status(created.hash_id, status)
    return created.hash_id


async def force_job_status(hash_id: str, status: str) -> None:
    """Drive a Job row directly to a target status via the lifecycle.

    The lifecycle is the only legal writer for already-persisted rows, so
    going through it ensures the batch fan-in hook fires (and SSE
    progress events too, when a sink is installed).
    """
    from app.domain.job_lifecycle import RUNNING, get_job_lifecycle

    lc = get_job_lifecycle()
    # Step through QUEUED → RUNNING when the target is a non-running terminal.
    if status in ("SUCCEEDED", "FAILED"):
        try:
            await lc.transition(hash_id, RUNNING, reason="test_step")
        except Exception:
            pass  # already past QUEUED
    await lc.transition(hash_id, status, reason="test_force")


async def fetch_batch_row(batch_id: str):
    from app.db.engine import get_session
    from app.db.models import Batch

    async with get_session() as session:
        return (
            await session.execute(select(Batch).where(Batch.id == batch_id))
        ).scalar_one_or_none()


async def time_travel_batch_activity(batch_id: str, seconds_into_past: int) -> None:
    """Push ``last_activity_at`` of a batch into the past so the watchdog catches it."""
    from app.db.engine import get_session
    from app.db.models import Batch

    when = datetime.now(timezone.utc) - timedelta(seconds=seconds_into_past)
    async with get_session() as session:
        await session.execute(
            update(Batch)
            .where(Batch.id == batch_id)
            .values(last_activity_at=when, updated_at=when)
        )


# ---------------------------------------------------------------------------
# SSE capture
# ---------------------------------------------------------------------------


class CapturedEvent:
    """One SSE-published event captured during a test."""

    __slots__ = ("user_id", "kind", "payload")

    def __init__(self, user_id: str, kind: str, payload: dict[str, Any]) -> None:
        self.user_id = user_id
        self.kind = kind
        self.payload = payload


def install_sse_capture() -> list[CapturedEvent]:
    """Replace the SSE hub's broadcast with a recorder. Returns the list."""
    from app.domain import sse_hub as _hub_mod

    captured: list[CapturedEvent] = []
    hub = _hub_mod.get_sse_hub()
    original = hub.broadcast_to_user

    async def fake(user_id: str, event: str, payload):
        captured.append(CapturedEvent(user_id, event, dict(payload)))
        # Still call the real one so the in-process replay buffer keeps
        # working for any other test depending on it.
        await original(user_id, event, payload)

    hub.broadcast_to_user = fake  # type: ignore[assignment]
    return captured


async def flush_emitter() -> None:
    """Wait for the debounced batch_progress emitter to drain."""
    from app.domain.batch_service import get_batch_progress_emitter

    await get_batch_progress_emitter().flush_for_tests()
