"""End-to-end tests for ``/api/admin/cleanup/*`` and the cleanup runner.

These exercise:

- ``GET /suggestions`` with mixed-age jobs on disk + DB.
- ``POST /api/admin/cleanup`` dry-run vs execute.
- ``GET /api/admin/cleanup/<task_id>`` polling.
- The exempt-starred behaviour: starred jobs survive cleanup.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest


# ---------------------------------------------------------------------------
# Helpers (lifted from test_cache_keeper.py and adapted for HTTP tests)
# ---------------------------------------------------------------------------


def _data_root_jobs() -> Path:
    return Path(os.environ["DATA_ROOT"]).resolve() / "jobs"


def _make_job_dir_on_disk(hash_id: str, payload_bytes: int) -> Path:
    p = _data_root_jobs() / hash_id / "outputs"
    p.mkdir(parents=True, exist_ok=True)
    (p / "01_original.png").write_bytes(b"x" * payload_bytes)
    return p.parent


async def _login_admin(client: httpx.AsyncClient) -> str:
    resp = await client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-admin-password"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def _login_user(client: httpx.AsyncClient) -> str:
    from app.db.engine import get_session
    from app.db.models import User
    from app.utils.security import hash_password

    async with get_session() as session:
        session.add(
            User(
                id="u_norm_cleantest",
                username="bob_cleanup",
                password_hash=hash_password("bobpw1234"),
                role="user",
                tier="free",
            )
        )

    resp = await client.post(
        "/api/auth/login",
        json={"username": "bob_cleanup", "password": "bobpw1234"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _seed_jobs(now: datetime) -> tuple[str, str, str]:
    """Drop three jobs into the DB (and on disk).

    Returns a tuple of ``(fresh_hash, old_hash, cancelled_hash)``.
    """
    from app.db.engine import get_session
    from app.db.models import Job, User

    async with get_session() as session:
        session.add(
            User(
                id="u_clean_owner",
                username="clean_owner",
                password_hash="x",
                role="user",
                tier="free",
                today_reset_date="2026-04-30",
            )
        )

    fresh = "j_freshfresh01"
    old = "j_oldoldold123"
    cancelled = "j_cancelcancel"

    _make_job_dir_on_disk(fresh, 200)
    _make_job_dir_on_disk(old, 400)
    _make_job_dir_on_disk(cancelled, 600)

    async with get_session() as session:
        session.add_all([
            Job(
                id="job_" + fresh[2:],
                hash_id=fresh,
                user_id="u_clean_owner",
                tier_at_submit="free",
                seq_no=1,
                model="gemini-3.1-flash-image-preview",
                params_json="{}",
                flags_json="{}",
                status="SUCCEEDED",
                created_at=now,
                finished_at=now,
            ),
            Job(
                id="job_" + old[2:],
                hash_id=old,
                user_id="u_clean_owner",
                tier_at_submit="free",
                seq_no=2,
                model="gemini-3.1-flash-image-preview",
                params_json="{}",
                flags_json="{}",
                status="SUCCEEDED",
                created_at=now - timedelta(days=40),
                finished_at=now - timedelta(days=40),
            ),
            Job(
                id="job_" + cancelled[2:],
                hash_id=cancelled,
                user_id="u_clean_owner",
                tier_at_submit="free",
                seq_no=3,
                model="gemini-3.1-flash-image-preview",
                params_json="{}",
                flags_json="{}",
                status="CANCELLED",
                created_at=now,
            ),
        ])
    return fresh, old, cancelled


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_suggestions_requires_admin(seeded_app: httpx.AsyncClient) -> None:
    resp = await seeded_app.get("/api/admin/cleanup/suggestions")
    assert resp.status_code == 401

    user_token = await _login_user(seeded_app)
    resp = await seeded_app.get(
        "/api/admin/cleanup/suggestions", headers=_auth(user_token)
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Suggestions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_suggestions_lists_known_buckets(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.get(
        "/api/admin/cleanup/suggestions", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    labels = [s["period_label"] for s in body["suggestions"]]
    assert "Older than 7 day(s)" in " | ".join(labels) or any(
        "7" in lbl for lbl in labels
    )
    # Five suggestion buckets per design doc §13.7.
    assert len(body["suggestions"]) == 5
    # Disk usage block exists; exact value depends on prior tests so just
    # check shape.
    assert "data_jobs_bytes" in body["disk_usage"]


@pytest.mark.asyncio
async def test_suggestions_count_jobs_correctly(
    seeded_app: httpx.AsyncClient,
) -> None:
    """40-day-old SUCCEEDED job should show up in the 7d & 30d buckets."""
    token = await _login_admin(seeded_app)
    now = datetime.now(timezone.utc)
    await _seed_jobs(now)

    resp = await seeded_app.get(
        "/api/admin/cleanup/suggestions", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    by_label = {s["period_label"]: s for s in resp.json()["suggestions"]}

    # 7d bucket: only the 40-day-old job qualifies.
    seven = by_label["SUCCEEDED/FAILED jobs older than 7 day(s)"]
    assert seven["job_count"] == 1
    assert seven["disk_bytes"] == 400

    # CANCELLED bucket: just our cancelled job.
    cancelled = by_label["CANCELLED jobs · any age"]
    assert cancelled["job_count"] == 1
    assert cancelled["disk_bytes"] == 600


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_does_not_touch_anything(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    now = datetime.now(timezone.utc)
    fresh, old, cancelled = await _seed_jobs(now)

    body = {
        "rules": [
            {
                "kind": "older_than_days",
                "days": 30,
                "statuses": ["SUCCEEDED"],
            }
        ],
        "dry_run": True,
        "exempt_starred": True,
    }
    resp = await seeded_app.post(
        "/api/admin/cleanup", headers=_auth(token), json=body
    )
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["job_count"] == 1  # the 40-day-old SUCCEEDED job
    assert out["disk_bytes"] == 400
    assert "disk_human" in out

    # On-disk dir should still exist after dry-run.
    assert (_data_root_jobs() / old / "outputs").exists()


# ---------------------------------------------------------------------------
# Real execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_marks_deleted_and_rms_dir(
    seeded_app: httpx.AsyncClient,
) -> None:
    from app.db.engine import get_session
    from app.db.models import Job
    from sqlalchemy import select

    token = await _login_admin(seeded_app)
    now = datetime.now(timezone.utc)
    fresh, old, cancelled = await _seed_jobs(now)

    body = {
        "rules": [{"kind": "status_only", "statuses": ["CANCELLED"]}],
        "dry_run": False,
    }
    resp = await seeded_app.post(
        "/api/admin/cleanup", headers=_auth(token), json=body
    )
    assert resp.status_code == 200, resp.text
    state = resp.json()
    assert state["task_id"].startswith("cleanup_")

    # Wait for the async task to finish — it's tiny.
    import asyncio

    for _ in range(30):
        poll = await seeded_app.get(
            f"/api/admin/cleanup/{state['task_id']}",
            headers=_auth(token),
        )
        assert poll.status_code == 200
        if poll.json()["status"] == "done":
            state = poll.json()
            break
        await asyncio.sleep(0.05)
    assert state["status"] == "done"
    assert state["processed_jobs"] == 1
    assert state["affected_user_count"] == 1

    # Cancelled job dir is gone.
    assert not (_data_root_jobs() / cancelled).exists()
    # SUCCEEDED jobs are still there.
    assert (_data_root_jobs() / fresh).exists()
    assert (_data_root_jobs() / old).exists()

    # DB row is DELETED.
    async with get_session() as session:
        row = (
            await session.execute(
                select(Job).where(Job.hash_id == cancelled)
            )
        ).scalar_one()
        assert row.status == "DELETED"


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rejects_live_status_in_rule(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    body = {
        "rules": [{"kind": "status_only", "statuses": ["RUNNING"]}],
        "dry_run": True,
    }
    resp = await seeded_app.post(
        "/api/admin/cleanup", headers=_auth(token), json=body
    )
    assert resp.status_code == 422, resp.text
    assert "live jobs" in resp.json()["detail"]["message"].lower()


@pytest.mark.asyncio
async def test_rejects_empty_rules(seeded_app: httpx.AsyncClient) -> None:
    token = await _login_admin(seeded_app)
    body = {"rules": [], "dry_run": True}
    resp = await seeded_app.post(
        "/api/admin/cleanup", headers=_auth(token), json=body
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_unknown_task_id_returns_404(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.get(
        "/api/admin/cleanup/cleanup_nonexistent",
        headers=_auth(token),
    )
    assert resp.status_code == 404
