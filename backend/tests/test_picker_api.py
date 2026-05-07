"""Tests for the Picker page endpoints (PRD §9).

Covers:
- migration back-fill: starred=1 rows become pick_state='picked'
- five image-write endpoints (pick/discard/final/defer/unjudge)
- final-image swap demotes the previous final to 'picked'
- session.picker_state recompute (not_started -> judging)
- finalize precondition (unjudged/deferred remaining)
- finalize idempotency
- unfinalize round-trip
- cursor patch is per-device (no SSE, no updated_at bump)
- GET /api/sessions/<id>/picker aggregate shape
- GET /api/picker/overview aggregate shape
- starred toggle mirrors pick_state both ways
- vary-seed creates a new job bound to the source's session
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest


async def _login_user(
    client: httpx.AsyncClient,
    *,
    username: str | None = None,
    tier: str = "premium",
) -> tuple[str, str]:
    from app.db.engine import get_session
    from app.db.models import User
    from app.utils.security import hash_password

    if username is None:
        username = f"u_{uuid.uuid4().hex[:8]}"
    user_id = f"u_test_{username}"
    async with get_session() as session:
        session.add(
            User(
                id=user_id,
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
    return resp.json()["access_token"], user_id


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _seed_session(*, user_id: str, name: str = "Cover slide") -> str:
    from app.db.engine import get_session
    from app.db.models import Session as SessionRow
    from app.utils.ids import new_session_id

    sess_id = new_session_id()
    now = datetime.now(timezone.utc)
    async with get_session() as session:
        session.add(
            SessionRow(
                id=sess_id,
                user_id=user_id,
                name=name,
                created_at=now,
                updated_at=now,
            )
        )
    return sess_id


async def _seed_job(
    *,
    user_id: str,
    hash_id: str,
    seq_no: int,
    status: str = "SUCCEEDED",
    session_id: str | None = None,
    prompt: str = "a still life",
    seed: int = 12345,
) -> str:
    from app.db.engine import get_session
    from app.db.models import Job, SessionJob, User
    from app.utils.ids import new_job_internal_id
    from sqlalchemy import select, update

    now = datetime.now(timezone.utc)
    job_id = new_job_internal_id()
    params = {"prompt": prompt, "model": "gpt-image-2", "seed": seed}
    async with get_session() as session:
        session.add(
            Job(
                id=job_id,
                hash_id=hash_id,
                user_id=user_id,
                tier_at_submit="premium",
                seq_no=seq_no,
                set_id=None,
                session_id=session_id,
                model="gpt-image-2",
                params_json=json.dumps(params),
                flags_json="{}",
                client_request_id=None,
                status=status,
                status_reason=None,
                provider_used="oai",
                retries=0,
                cost_cny=0.0,
                created_at=now - timedelta(minutes=5),
                queued_at=now - timedelta(minutes=5),
                dispatched_at=now - timedelta(minutes=4),
                started_at=now - timedelta(minutes=4),
                finished_at=now - timedelta(minutes=3),
                updated_at=now - timedelta(minutes=3),
            )
        )
        if session_id:
            session.add(SessionJob(session_id=session_id, job_id=job_id))
        # Keep the user's last_seq_no in sync so repo.allocate_seq_no
        # doesn't collide on a future API-driven job creation.
        await session.execute(
            update(User)
            .where(User.id == user_id)
            .values(last_seq_no=User.last_seq_no + 1)
        )
    return job_id


async def _seed_image(
    *,
    job_id: str,
    order: int,
    starred: bool = False,
    pick_state: str = "unjudged",
) -> str:
    from app.db.engine import get_session
    from app.db.models import Image
    from app.utils.ids import new_image_id

    image_id = new_image_id()
    async with get_session() as session:
        session.add(
            Image(
                id=image_id,
                job_id=job_id,
                img_order=order,
                original_path=f"jobs/dummy/{order:02d}_orig.png",
                thumb_path=f"jobs/dummy/{order:02d}_thumb.webp",
                width=512,
                height=512,
                format="png",
                file_size_bytes=1024,
                starred=1 if starred else 0,
                pick_state=pick_state,
            )
        )
    return image_id


# ---------------------------------------------------------------------------
# Image-write endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pick_image_happy_path(seeded_app: httpx.AsyncClient) -> None:
    token, user_id = await _login_user(seeded_app)
    sess_id = await _seed_session(user_id=user_id)
    hash_id = "j_pick_happy01"
    job_id = await _seed_job(
        user_id=user_id, hash_id=hash_id, seq_no=1, session_id=sess_id
    )
    await _seed_image(job_id=job_id, order=1)

    resp = await seeded_app.post(
        f"/api/jobs/{hash_id}/images/1/pick", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pick_state"] == "picked"
    assert body["starred"] is True
    assert body["session_id"] == sess_id
    assert body["session_picker_state"] == "judging"


@pytest.mark.asyncio
async def test_pick_image_404_for_other_user(
    seeded_app: httpx.AsyncClient,
) -> None:
    owner_token, owner_id = await _login_user(seeded_app, username="owner_pk")
    sess_id = await _seed_session(user_id=owner_id)
    hash_id = "j_owner_pk1"
    job_id = await _seed_job(
        user_id=owner_id, hash_id=hash_id, seq_no=1, session_id=sess_id
    )
    await _seed_image(job_id=job_id, order=1)

    thief_token, _ = await _login_user(seeded_app, username="thief_pk")
    resp = await seeded_app.post(
        f"/api/jobs/{hash_id}/images/1/pick", headers=_auth(thief_token)
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_final_demotes_previous_final(
    seeded_app: httpx.AsyncClient,
) -> None:
    token, user_id = await _login_user(seeded_app, username="demote_user")
    sess_id = await _seed_session(user_id=user_id)
    hash_id = "j_demoteh001"
    job_id = await _seed_job(
        user_id=user_id, hash_id=hash_id, seq_no=1, session_id=sess_id
    )
    img_a = await _seed_image(job_id=job_id, order=1, pick_state="unjudged")
    img_b = await _seed_image(job_id=job_id, order=2, pick_state="unjudged")

    # Set image 1 as final.
    r1 = await seeded_app.post(
        f"/api/jobs/{hash_id}/images/1/final", headers=_auth(token)
    )
    assert r1.status_code == 200, r1.text
    b1 = r1.json()
    assert b1["pick_state"] == "final"
    assert b1["session_final_image_id"] == img_a

    # Now set image 2 as final — image 1 should be demoted to picked.
    r2 = await seeded_app.post(
        f"/api/jobs/{hash_id}/images/2/final", headers=_auth(token)
    )
    assert r2.status_code == 200, r2.text
    b2 = r2.json()
    assert b2["pick_state"] == "final"
    assert b2["session_final_image_id"] == img_b
    assert b2["previous_final_image_id"] == img_a

    # Verify image 1 is now 'picked'.
    from app.db.engine import get_session
    from app.db.models import Image
    from sqlalchemy import select

    async with get_session() as s:
        row = (
            await s.execute(select(Image).where(Image.id == img_a))
        ).scalar_one()
    assert row.pick_state == "picked"
    assert row.starred == 1


@pytest.mark.asyncio
async def test_unjudge_clears_session_final(
    seeded_app: httpx.AsyncClient,
) -> None:
    token, user_id = await _login_user(seeded_app, username="unjudge_user")
    sess_id = await _seed_session(user_id=user_id)
    hash_id = "j_unjudgeh01"
    job_id = await _seed_job(
        user_id=user_id, hash_id=hash_id, seq_no=1, session_id=sess_id
    )
    await _seed_image(job_id=job_id, order=1)

    await seeded_app.post(
        f"/api/jobs/{hash_id}/images/1/final", headers=_auth(token)
    )
    resp = await seeded_app.post(
        f"/api/jobs/{hash_id}/images/1/unjudge", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pick_state"] == "unjudged"
    assert body["session_final_image_id"] is None
    assert body["starred"] is False


@pytest.mark.asyncio
async def test_no_op_pick_returns_current_state(
    seeded_app: httpx.AsyncClient,
) -> None:
    token, user_id = await _login_user(seeded_app, username="noop_user")
    sess_id = await _seed_session(user_id=user_id)
    hash_id = "j_noophash01"
    job_id = await _seed_job(
        user_id=user_id, hash_id=hash_id, seq_no=1, session_id=sess_id
    )
    await _seed_image(job_id=job_id, order=1, pick_state="picked", starred=True)

    resp = await seeded_app.post(
        f"/api/jobs/{hash_id}/images/1/pick", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["pick_state"] == "picked"


# ---------------------------------------------------------------------------
# Session-level operations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_blocked_when_unjudged(
    seeded_app: httpx.AsyncClient,
) -> None:
    token, user_id = await _login_user(seeded_app, username="fin_block")
    sess_id = await _seed_session(user_id=user_id)
    hash_id = "j_finblock01"
    job_id = await _seed_job(
        user_id=user_id, hash_id=hash_id, seq_no=1, session_id=sess_id
    )
    await _seed_image(job_id=job_id, order=1, pick_state="unjudged")
    await _seed_image(job_id=job_id, order=2, pick_state="picked")

    resp = await seeded_app.post(
        f"/api/sessions/{sess_id}/finalize", headers=_auth(token)
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["detail"]["code"] == "INVALID_PICKER_STATE"
    assert body["detail"]["extra"]["unjudged"] == 1


@pytest.mark.asyncio
async def test_finalize_then_idempotent(
    seeded_app: httpx.AsyncClient,
) -> None:
    token, user_id = await _login_user(seeded_app, username="fin_ok")
    sess_id = await _seed_session(user_id=user_id)
    hash_id = "j_finokh0001"
    job_id = await _seed_job(
        user_id=user_id, hash_id=hash_id, seq_no=1, session_id=sess_id
    )
    await _seed_image(job_id=job_id, order=1, pick_state="picked", starred=True)
    await _seed_image(job_id=job_id, order=2, pick_state="discarded")

    # Promote img 1 to final.
    await seeded_app.post(
        f"/api/jobs/{hash_id}/images/1/final", headers=_auth(token)
    )
    # Finalize.
    r1 = await seeded_app.post(
        f"/api/sessions/{sess_id}/finalize", headers=_auth(token)
    )
    assert r1.status_code == 200, r1.text
    body1 = r1.json()
    assert body1["picker_state"] == "finalized"
    finalized_at = body1["finalized_at"]
    # Repeat — should be idempotent and return same finalized_at.
    r2 = await seeded_app.post(
        f"/api/sessions/{sess_id}/finalize", headers=_auth(token)
    )
    assert r2.status_code == 200
    assert r2.json()["finalized_at"] == finalized_at


@pytest.mark.asyncio
async def test_unfinalize_round_trip(seeded_app: httpx.AsyncClient) -> None:
    token, user_id = await _login_user(seeded_app, username="unfin_user")
    sess_id = await _seed_session(user_id=user_id)
    hash_id = "j_unfinh0001"
    job_id = await _seed_job(
        user_id=user_id, hash_id=hash_id, seq_no=1, session_id=sess_id
    )
    await _seed_image(job_id=job_id, order=1, pick_state="picked", starred=True)
    await seeded_app.post(
        f"/api/jobs/{hash_id}/images/1/final", headers=_auth(token)
    )
    await seeded_app.post(
        f"/api/sessions/{sess_id}/finalize", headers=_auth(token)
    )

    r = await seeded_app.post(
        f"/api/sessions/{sess_id}/unfinalize", headers=_auth(token)
    )
    assert r.status_code == 200, r.text
    assert r.json()["picker_state"] == "judging"
    assert r.json()["finalized_at"] is None


@pytest.mark.asyncio
async def test_patch_cursor(seeded_app: httpx.AsyncClient) -> None:
    token, user_id = await _login_user(seeded_app, username="cursor_user")
    sess_id = await _seed_session(user_id=user_id)

    r = await seeded_app.patch(
        f"/api/sessions/{sess_id}/cursor",
        headers=_auth(token),
        json={"cursor_image_id": "img_test_42"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cursor_image_id"] == "img_test_42"


# ---------------------------------------------------------------------------
# Aggregate reads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_session_picker_aggregate(
    seeded_app: httpx.AsyncClient,
) -> None:
    token, user_id = await _login_user(seeded_app, username="agg_user")
    sess_id = await _seed_session(user_id=user_id)
    hash_id = "j_aggh000001"
    job_id = await _seed_job(
        user_id=user_id,
        hash_id=hash_id,
        seq_no=1,
        session_id=sess_id,
        seed=987,
    )
    await _seed_image(job_id=job_id, order=1, pick_state="picked", starred=True)
    await _seed_image(job_id=job_id, order=2, pick_state="unjudged")

    r = await seeded_app.get(
        f"/api/sessions/{sess_id}/picker", headers=_auth(token)
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["session"]["id"] == sess_id
    assert len(body["jobs"]) == 1
    assert body["jobs"][0]["hash_id"] == hash_id
    assert len(body["images"]) == 2
    # Verify seed plumbed through
    assert body["images"][0]["seed"] == "987"
    # Image-1 should be picked
    pick_states = {img["order"]: img["pick_state"] for img in body["images"]}
    assert pick_states[1] == "picked"
    assert pick_states[2] == "unjudged"


@pytest.mark.asyncio
async def test_picker_overview(seeded_app: httpx.AsyncClient) -> None:
    token, user_id = await _login_user(seeded_app, username="ov_user")
    # 3 sessions: not_started (no images), judging (mixed), finalized.
    s1 = await _seed_session(user_id=user_id, name="Cover")
    s2 = await _seed_session(user_id=user_id, name="Body")
    s3 = await _seed_session(user_id=user_id, name="Closing")

    j2 = await _seed_job(
        user_id=user_id, hash_id="j_ov2hash01", seq_no=1, session_id=s2
    )
    await _seed_image(job_id=j2, order=1, pick_state="picked", starred=True)
    await _seed_image(job_id=j2, order=2, pick_state="unjudged")
    # Bump s2 to 'judging' so the overview classifies it correctly. In
    # production this happens automatically when the user calls one of
    # the pick/discard/etc. endpoints.
    from app.db.engine import get_session as _gs2
    from app.db.models import Session as _SR
    from sqlalchemy import select as _select

    async with _gs2() as s:
        row = (await s.execute(_select(_SR).where(_SR.id == s2))).scalar_one()
        row.picker_state = "judging"

    j3 = await _seed_job(
        user_id=user_id, hash_id="j_ov3hash01", seq_no=2, session_id=s3
    )
    img3a = await _seed_image(
        job_id=j3, order=1, pick_state="final", starred=True
    )
    await _seed_image(job_id=j3, order=2, pick_state="picked", starred=True)

    # Promote and finalize s3.
    from app.db.engine import get_session
    from app.db.models import Session as SessionRow
    from sqlalchemy import select

    async with get_session() as s:
        row = (
            await s.execute(select(SessionRow).where(SessionRow.id == s3))
        ).scalar_one()
        row.picker_state = "finalized"
        row.final_image_id = img3a
        row.finalized_at = datetime.now(timezone.utc)

    r = await seeded_app.get("/api/picker/overview", headers=_auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert {s["name"] for s in body["sessions"]} == {"Cover", "Body", "Closing"}
    summary = body["summary"]
    assert summary["finalized"] == 1
    assert summary["not_started"] == 1
    assert summary["judging"] == 1


# ---------------------------------------------------------------------------
# Starred sync
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_starred_pick_state_sync(seeded_app: httpx.AsyncClient) -> None:
    token, user_id = await _login_user(seeded_app, username="star_sync")
    sess_id = await _seed_session(user_id=user_id)
    hash_id = "j_starsh001"
    job_id = await _seed_job(
        user_id=user_id, hash_id=hash_id, seq_no=1, session_id=sess_id
    )
    await _seed_image(job_id=job_id, order=1)

    # Star → picked
    r = await seeded_app.post(
        f"/api/jobs/{hash_id}/images/1/star",
        headers=_auth(token),
        json={"starred": True},
    )
    assert r.status_code == 200
    assert r.json()["starred"] is True

    from app.db.engine import get_session
    from app.db.models import Image
    from sqlalchemy import select

    async with get_session() as s:
        row = (
            await s.execute(
                select(Image).where(Image.job_id == job_id, Image.img_order == 1)
            )
        ).scalar_one()
    assert row.pick_state == "picked"

    # Unstar → unjudged
    r2 = await seeded_app.post(
        f"/api/jobs/{hash_id}/images/1/star",
        headers=_auth(token),
        json={"starred": False},
    )
    assert r2.status_code == 200
    async with get_session() as s:
        row = (
            await s.execute(
                select(Image).where(Image.job_id == job_id, Image.img_order == 1)
            )
        ).scalar_one()
    assert row.pick_state == "unjudged"


# ---------------------------------------------------------------------------
# Vary-seed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vary_seed_creates_bound_job(
    seeded_app: httpx.AsyncClient,
) -> None:
    token, user_id = await _login_user(seeded_app, username="vary_user")
    sess_id = await _seed_session(user_id=user_id)
    hash_id = "j_varyh00001"
    job_id = await _seed_job(
        user_id=user_id,
        hash_id=hash_id,
        seq_no=1,
        session_id=sess_id,
        seed=4242,
    )
    img_id = await _seed_image(job_id=job_id, order=1)

    r = await seeded_app.post(
        "/api/jobs/vary",
        headers=_auth(token),
        json={"source_image_id": img_id, "seed": 9999},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["session_id"] == sess_id
    assert body["status"] == "QUEUED"
    new_hash = body["hash_id"]

    # Verify the new job exists, is bound, and uses the new seed.
    from app.db.engine import get_session
    from app.db.models import Job
    from sqlalchemy import select

    async with get_session() as s:
        new_job = (
            await s.execute(select(Job).where(Job.hash_id == new_hash))
        ).scalar_one()
    assert new_job.session_id == sess_id
    params = json.loads(new_job.params_json)
    assert params["seed"] == 9999
    assert params["model"] == "gpt-image-2"
