"""E2E for the admin cleanup contrast (dry_run vs real delete) and the
admin happy-path covering tiers / providers / config / audit.

These two areas share enough setup that one file is easier to maintain
than splitting on a thousand helpers. Both files in this module use
the seeded admin account.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.db.engine import get_session
from app.db.models import Job, User

from .conftest import (
    auth_header,
    job_payload,
    login_admin,
    login_user,
    run_scheduler_until_idle,
    seed_provider,
    submit_job,
    wait_for_status,
)


# ---------------------------------------------------------------------------
# Cleanup E2E
# ---------------------------------------------------------------------------


async def _seed_old_job(hash_id: str, *, days_ago: int) -> None:
    """Drop a SUCCEEDED job whose finished_at is ``days_ago`` days old."""
    now = datetime.now(timezone.utc)
    finished = now - timedelta(days=days_ago)
    user_id = "u_cleanup_owner"
    async with get_session() as session:
        existing = (
            await session.execute(
                select(User.id).where(User.id == user_id)
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                User(
                    id=user_id,
                    username="cleanup_owner",
                    password_hash="x",
                    role="user",
                    tier="free",
                )
            )

        # Atomic seq_no allocation per user.
        from sqlalchemy import update as _update

        row = (
            await session.execute(
                _update(User)
                .where(User.id == user_id)
                .values(last_seq_no=User.last_seq_no + 1)
                .returning(User.last_seq_no)
            )
        ).one_or_none()
        seq = int(row[0]) if row else 1

        session.add(
            Job(
                id=f"job_{hash_id[-8:]}",
                hash_id=hash_id,
                user_id=user_id,
                tier_at_submit="free",
                seq_no=seq,
                model="gpt-image-2",
                params_json="{}",
                flags_json="{}",
                status="SUCCEEDED",
                created_at=finished,
                finished_at=finished,
                updated_at=finished,
            )
        )

    # Sprinkle a tiny output file so disk_usage rolls something up.
    p = Path(os.environ["DATA_ROOT"]).resolve() / "jobs" / hash_id / "outputs"
    p.mkdir(parents=True, exist_ok=True)
    (p / "01_original.png").write_bytes(b"x" * 256)


@pytest.mark.asyncio
async def test_cleanup_dry_run_does_not_delete(
    seeded_app: httpx.AsyncClient,
) -> None:
    admin_token = await login_admin(seeded_app)
    await _seed_old_job("j_cleanold0001", days_ago=60)

    rules_payload = {
        "rules": [
            {"kind": "older_than_days", "days": 30, "statuses": ["SUCCEEDED"]}
        ],
        "dry_run": True,
    }
    resp = await seeded_app.post(
        "/api/admin/cleanup", headers=auth_header(admin_token), json=rules_payload
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Dry-run response has counts, no task_id (real-run path returns task_id).
    assert body.get("job_count", 0) >= 1
    assert "task_id" not in body

    # Job + dir still exist.
    async with get_session() as session:
        row = (
            await session.execute(
                select(Job.status).where(Job.hash_id == "j_cleanold0001")
            )
        ).scalar_one()
    assert row == "SUCCEEDED"
    assert (Path(os.environ["DATA_ROOT"]).resolve() / "jobs" / "j_cleanold0001").exists()


@pytest.mark.asyncio
async def test_cleanup_run_marks_deleted_and_removes_dir(
    seeded_app: httpx.AsyncClient,
) -> None:
    admin_token = await login_admin(seeded_app)
    await _seed_old_job("j_cleanold0002", days_ago=60)

    rules_payload = {
        "rules": [
            {"kind": "older_than_days", "days": 30, "statuses": ["SUCCEEDED"]}
        ],
        "dry_run": False,
    }
    resp = await seeded_app.post(
        "/api/admin/cleanup", headers=auth_header(admin_token), json=rules_payload
    )
    assert resp.status_code == 200, resp.text
    task_id = resp.json().get("task_id")
    assert task_id

    # Poll for completion.
    import asyncio

    deadline = asyncio.get_event_loop().time() + 5.0
    while asyncio.get_event_loop().time() < deadline:
        prog = await seeded_app.get(
            f"/api/admin/cleanup/{task_id}", headers=auth_header(admin_token)
        )
        if prog.status_code == 200 and prog.json().get("status") in {
            "completed",
            "succeeded",
            "done",
        }:
            break
        await asyncio.sleep(0.05)

    async with get_session() as session:
        row = (
            await session.execute(
                select(Job.status).where(Job.hash_id == "j_cleanold0002")
            )
        ).scalar_one()
    assert row == "DELETED"
    # Directory should be gone (best-effort; the cleanup helper rm-rfs it).
    job_dir = (
        Path(os.environ["DATA_ROOT"]).resolve() / "jobs" / "j_cleanold0002"
    )
    assert not job_dir.exists()


# ---------------------------------------------------------------------------
# Admin happy-path tour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_happy_path(seeded_app: httpx.AsyncClient) -> None:
    """Walk the admin surface that operators rely on day-to-day.

    - Tier list / patch
    - Provider create / list / topup / delete
    - Config patch (a non-emergency key)
    - Audit log shows our activity
    """
    admin_token = await login_admin(seeded_app)

    # Tiers
    resp = await seeded_app.get("/api/admin/tiers", headers=auth_header(admin_token))
    assert resp.status_code == 200
    body = resp.json()
    tiers = body.get("tiers", body) if isinstance(body, dict) else body
    assert any(t["tier"] == "vip" for t in tiers)

    # Patch a tier.
    resp = await seeded_app.patch(
        "/api/admin/tiers/free",
        headers=auth_header(admin_token),
        json={"max_concurrency": 2},
    )
    assert resp.status_code == 200, resp.text

    # Providers — use a real adapter type since this test doesn't stub.
    await seed_provider(
        seeded_app,
        provider_id="oai_admin",
        label="Admin",
        adapter_type="openai_v1",
    )
    listed = await seeded_app.get(
        "/api/admin/providers", headers=auth_header(admin_token)
    )
    assert listed.status_code == 200
    providers = listed.json()
    assert any(p["id"] == "oai_admin" for p in providers)

    # Topup
    topup = await seeded_app.post(
        "/api/admin/providers/oai_admin/topup",
        headers=auth_header(admin_token),
        json={"amount_cny": 5.0},
    )
    assert topup.status_code == 200, topup.text

    # Config patch (a safe non-emergency key).
    cfg = await seeded_app.patch(
        "/api/admin/config",
        headers=auth_header(admin_token),
        json={"scheduler.global_max_workers": 24},
    )
    assert cfg.status_code == 200, cfg.text

    # Audit log: our admin actions should appear.
    audit = await seeded_app.get(
        "/api/admin/audit", headers=auth_header(admin_token)
    )
    assert audit.status_code == 200
    rows = audit.json().get("items", [])
    actions = {row.get("action") for row in rows}
    # Some core actions we definitely just took:
    assert any(a for a in actions if "provider" in (a or ""))
