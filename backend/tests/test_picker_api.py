"""End-to-end tests for the PRD-v1 Picker API.

Coverage:

- DB migration: ``images.pick_state`` defaults to ``unjudged``.
- Per-image transitions (pick / discard / final / defer / unjudge) keep
  ``starred`` in sync per PRD §8.5.1.
- Setting a new ``final`` demotes the previous ``final`` to ``picked``.
- ``GET /api/sessions/<id>/picker`` returns one shot of jobs + images.
- ``POST /api/sessions/<id>/finalize`` enforces preconditions, is
  idempotent on a finalized session.
- ``GET /api/picker/overview`` returns deck stats + ready_to_finalize.
- Cross-tenant access returns 404.

Direct DB seeding follows the same pattern as ``test_archive_api.py``
so the picker tests don't pull in the executor / provider stack.
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest


# ---------------------------------------------------------------------------
# Login helper
# ---------------------------------------------------------------------------


async def _login_user(
    client: httpx.AsyncClient,
    *,
    username: str = "alice",
    tier: str = "premium",
) -> tuple[str, str]:
    from app.db.engine import get_session
    from app.db.models import User
    from app.utils.security import hash_password

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


# ---------------------------------------------------------------------------
# Direct DB seeding
# ---------------------------------------------------------------------------


async def _seed_session(
    *,
    user_id: str,
    name: str = "test session",
    picker_state: str = "not_started",
    final_image_id: str | None = None,
) -> str:
    from app.db.engine import get_session
    from app.db.models import Session as SessionRow
    from app.utils.ids import new_session_id

    sid = new_session_id()
    now = datetime.now(timezone.utc)
    async with get_session() as session:
        session.add(
            SessionRow(
                id=sid,
                user_id=user_id,
                name=name,
                picker_state=picker_state,
                final_image_id=final_image_id,
                created_at=now,
                updated_at=now,
            )
        )
    return sid


async def _seed_job(
    *,
    user_id: str,
    hash_id: str,
    seq_no: int,
    status: str = "SUCCEEDED",
    session_id: str | None = None,
    model: str = "gpt-image-2",
    prompt: str = "a still life",
    minutes_ago: int = 5,
) -> str:
    import json

    from app.db.engine import get_session
    from app.db.models import Job, SessionJob
    from app.utils.ids import new_job_internal_id

    now = datetime.now(timezone.utc)
    queued_at = now - timedelta(minutes=minutes_ago)

    job_id = new_job_internal_id()
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
                model=model,
                params_json=json.dumps(
                    {"prompt": prompt, "model": model, "seed": "abc-123"}
                ),
                flags_json="{}",
                client_request_id=None,
                status=status,
                status_reason=None,
                provider_used="oai",
                retries=0,
                cost_cny=0.0,
                created_at=queued_at,
                queued_at=queued_at,
                dispatched_at=queued_at,
                started_at=queued_at,
                finished_at=now if status == "SUCCEEDED" else None,
                updated_at=now,
            )
        )
        if session_id:
            session.add(SessionJob(session_id=session_id, job_id=job_id))
    return job_id


async def _seed_image_on_disk(
    *,
    job_id: str,
    hash_id: str,
    order: int,
    starred: bool = False,
    pick_state: str = "unjudged",
) -> str:
    """Image io requires order >= 1, so callers always pass 1, 2, ..."""
    from PIL import Image as PILImage

    from app.config import get_settings
    from app.db.engine import get_session
    from app.db.models import Image
    from app.services import image_io
    from app.utils.ids import new_image_id

    image_io.ensure_job_dirs(hash_id)
    buf = io.BytesIO()
    PILImage.new("RGB", (32, 32), color=(123, 200, 80)).save(
        buf, format="PNG"
    )
    rel_orig = image_io.save_original(
        hash_id, order, buf.getvalue(), "image/png"
    )
    abs_orig = image_io.path_for_output_original(hash_id, order, "png")
    abs_thumb = image_io.path_for_output_thumb(hash_id, order)
    image_io.make_thumbnail(abs_orig, abs_thumb)

    data_root = Path(get_settings().DATA_ROOT).resolve()
    rel_thumb = str(abs_thumb.relative_to(data_root))

    image_id = new_image_id()
    async with get_session() as session:
        session.add(
            Image(
                id=image_id,
                job_id=job_id,
                img_order=order,
                original_path=rel_orig,
                thumb_path=rel_thumb,
                width=32,
                height=32,
                format="png",
                file_size_bytes=abs_orig.stat().st_size,
                starred=1 if starred else 0,
                pick_state=pick_state,
            )
        )
    return image_id


# ---------------------------------------------------------------------------
# Migration: default state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pick_state_defaults_to_unjudged(
    seeded_app: httpx.AsyncClient,
) -> None:
    _, user_id = await _login_user(seeded_app, username="default_state")
    sid = await _seed_session(user_id=user_id)
    job_id = await _seed_job(
        user_id=user_id, hash_id="j_dfltpst12345", seq_no=1, session_id=sid
    )
    await _seed_image_on_disk(
        job_id=job_id, hash_id="j_dfltpst12345", order=1
    )

    from app.db.engine import get_session
    from app.db.models import Image
    from sqlalchemy import select

    async with get_session() as session:
        img = (
            await session.execute(select(Image).where(Image.job_id == job_id))
        ).scalar_one()
    assert img.pick_state == "unjudged"
    assert img.pick_state_updated_at is None
    assert img.starred == 0


# ---------------------------------------------------------------------------
# Per-image transitions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pick_endpoint_sets_state_and_starred(
    seeded_app: httpx.AsyncClient,
) -> None:
    token, user_id = await _login_user(seeded_app, username="picker_pick")
    sid = await _seed_session(user_id=user_id)
    job_id = await _seed_job(
        user_id=user_id, hash_id="j_pickerpick12", seq_no=1, session_id=sid
    )
    await _seed_image_on_disk(
        job_id=job_id, hash_id="j_pickerpick12", order=1
    )

    resp = await seeded_app.post(
        "/api/jobs/j_pickerpick12/images/1/pick", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pick_state"] == "picked"
    assert body["starred"] is True
    assert body["session_picker_state"] == "judging"


@pytest.mark.asyncio
async def test_discard_clears_starred(
    seeded_app: httpx.AsyncClient,
) -> None:
    token, user_id = await _login_user(seeded_app, username="picker_discard")
    sid = await _seed_session(user_id=user_id)
    job_id = await _seed_job(
        user_id=user_id, hash_id="j_pickerdisc12", seq_no=1, session_id=sid
    )
    await _seed_image_on_disk(
        job_id=job_id, hash_id="j_pickerdisc12", order=1,
        starred=True, pick_state="picked",
    )

    resp = await seeded_app.post(
        "/api/jobs/j_pickerdisc12/images/1/discard", headers=_auth(token)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["pick_state"] == "discarded"
    assert body["starred"] is False


@pytest.mark.asyncio
async def test_final_demotes_previous_final(
    seeded_app: httpx.AsyncClient,
) -> None:
    token, user_id = await _login_user(seeded_app, username="picker_swap_fin")
    sid = await _seed_session(user_id=user_id)
    job_id = await _seed_job(
        user_id=user_id, hash_id="j_pfswapaaaaab", seq_no=1, session_id=sid
    )
    img_a = await _seed_image_on_disk(
        job_id=job_id, hash_id="j_pfswapaaaaab", order=1,
        starred=True, pick_state="final",
    )
    await _seed_image_on_disk(
        job_id=job_id, hash_id="j_pfswapaaaaab", order=2,
    )

    from app.db.engine import get_session
    from app.db.models import Session as SessionRow
    from sqlalchemy import update as sql_update

    async with get_session() as session:
        await session.execute(
            sql_update(SessionRow)
            .where(SessionRow.id == sid)
            .values(final_image_id=img_a)
        )

    resp = await seeded_app.post(
        "/api/jobs/j_pfswapaaaaab/images/2/final", headers=_auth(token)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["pick_state"] == "final"
    assert body["session_final_image_id"] != img_a
    assert body["previous_final_image_id"] == img_a

    from app.db.models import Image
    from sqlalchemy import select

    async with get_session() as session:
        a_row = (
            await session.execute(select(Image).where(Image.id == img_a))
        ).scalar_one()
    assert a_row.pick_state == "picked"
    assert a_row.starred == 1


# ---------------------------------------------------------------------------
# Star toggle mirror (PRD §8.5.3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_star_toggle_promotes_to_picked(
    seeded_app: httpx.AsyncClient,
) -> None:
    token, user_id = await _login_user(seeded_app, username="star_to_pick")
    sid = await _seed_session(user_id=user_id)
    job_id = await _seed_job(
        user_id=user_id, hash_id="j_starpromaaab", seq_no=1, session_id=sid
    )
    await _seed_image_on_disk(
        job_id=job_id, hash_id="j_starpromaaab", order=1
    )

    resp = await seeded_app.post(
        "/api/jobs/j_starpromaaab/images/1/star",
        headers=_auth(token),
        json={"starred": True},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["starred"] is True

    from app.db.engine import get_session
    from app.db.models import Image
    from sqlalchemy import select

    async with get_session() as session:
        img = (
            await session.execute(
                select(Image).where(Image.job_id == job_id)
            )
        ).scalar_one()
    assert img.pick_state == "picked"


# ---------------------------------------------------------------------------
# Session reads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_picker_returns_jobs_and_images(
    seeded_app: httpx.AsyncClient,
) -> None:
    token, user_id = await _login_user(seeded_app, username="picker_get_sess")
    sid = await _seed_session(user_id=user_id, name="cover slide")
    job_id = await _seed_job(
        user_id=user_id, hash_id="j_getsessaaaab", seq_no=1, session_id=sid,
        prompt="editorial cover banana"
    )
    await _seed_image_on_disk(
        job_id=job_id, hash_id="j_getsessaaaab", order=1
    )
    await _seed_image_on_disk(
        job_id=job_id, hash_id="j_getsessaaaab", order=2
    )

    resp = await seeded_app.get(
        f"/api/sessions/{sid}/picker", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["session"]["name"] == "cover slide"
    assert body["session"]["picker_state"] == "not_started"
    assert len(body["jobs"]) == 1
    assert body["jobs"][0]["prompt"] == "editorial cover banana"
    assert len(body["images"]) == 2
    assert body["images"][0]["pick_state"] == "unjudged"
    assert body["images"][0]["seed"] == "abc-123"
    assert body["images"][0]["thumb_url"].startswith("/api/jobs/")


@pytest.mark.asyncio
async def test_session_picker_returns_404_when_cross_tenant(
    seeded_app: httpx.AsyncClient,
) -> None:
    _, owner_id = await _login_user(seeded_app, username="ownr_sess_x")
    sid = await _seed_session(user_id=owner_id)
    intruder_token, _ = await _login_user(seeded_app, username="thief_sess")
    resp = await seeded_app.get(
        f"/api/sessions/{sid}/picker", headers=_auth(intruder_token)
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Finalize / unfinalize
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_rejects_when_unjudged(
    seeded_app: httpx.AsyncClient,
) -> None:
    token, user_id = await _login_user(seeded_app, username="picker_finz_no")
    sid = await _seed_session(user_id=user_id)
    job_id = await _seed_job(
        user_id=user_id, hash_id="j_finznoaaaaab", seq_no=1, session_id=sid
    )
    await _seed_image_on_disk(
        job_id=job_id, hash_id="j_finznoaaaaab", order=1
    )
    resp = await seeded_app.post(
        f"/api/sessions/{sid}/finalize", headers=_auth(token)
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "INVALID_PICKER_STATE"


@pytest.mark.asyncio
async def test_finalize_succeeds_when_ready(
    seeded_app: httpx.AsyncClient,
) -> None:
    token, user_id = await _login_user(seeded_app, username="picker_finz_ok")
    sid = await _seed_session(user_id=user_id)
    job_id = await _seed_job(
        user_id=user_id, hash_id="j_finzokaaaaab", seq_no=1, session_id=sid
    )
    img = await _seed_image_on_disk(
        job_id=job_id, hash_id="j_finzokaaaaab", order=1
    )

    pick = await seeded_app.post(
        "/api/jobs/j_finzokaaaaab/images/1/final", headers=_auth(token)
    )
    assert pick.status_code == 200

    resp = await seeded_app.post(
        f"/api/sessions/{sid}/finalize", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["picker_state"] == "finalized"
    assert body["final_image_id"] == img


@pytest.mark.asyncio
async def test_finalize_is_idempotent(
    seeded_app: httpx.AsyncClient,
) -> None:
    token, user_id = await _login_user(seeded_app, username="finz_idem")
    sid = await _seed_session(user_id=user_id)
    job_id = await _seed_job(
        user_id=user_id, hash_id="j_finzidemaaab", seq_no=1, session_id=sid
    )
    await _seed_image_on_disk(
        job_id=job_id, hash_id="j_finzidemaaab", order=1
    )
    pick = await seeded_app.post(
        "/api/jobs/j_finzidemaaab/images/1/final", headers=_auth(token)
    )
    assert pick.status_code == 200
    r1 = await seeded_app.post(
        f"/api/sessions/{sid}/finalize", headers=_auth(token)
    )
    r2 = await seeded_app.post(
        f"/api/sessions/{sid}/finalize", headers=_auth(token)
    )
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["picker_state"] == "finalized"
    assert r2.json()["picker_state"] == "finalized"


@pytest.mark.asyncio
async def test_unfinalize_drops_to_judging(
    seeded_app: httpx.AsyncClient,
) -> None:
    token, user_id = await _login_user(seeded_app, username="finz_unlock")
    sid = await _seed_session(user_id=user_id)
    job_id = await _seed_job(
        user_id=user_id, hash_id="j_finzunlockx2", seq_no=1, session_id=sid
    )
    await _seed_image_on_disk(
        job_id=job_id, hash_id="j_finzunlockx2", order=1
    )
    await seeded_app.post(
        "/api/jobs/j_finzunlockx2/images/1/final", headers=_auth(token)
    )
    await seeded_app.post(
        f"/api/sessions/{sid}/finalize", headers=_auth(token)
    )
    resp = await seeded_app.post(
        f"/api/sessions/{sid}/unfinalize", headers=_auth(token)
    )
    assert resp.status_code == 200
    assert resp.json()["picker_state"] == "judging"
    assert resp.json()["final_image_id"]  # preserved


# ---------------------------------------------------------------------------
# Cursor PATCH
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cursor_patch_persists(
    seeded_app: httpx.AsyncClient,
) -> None:
    token, user_id = await _login_user(seeded_app, username="cursor_patch")
    sid = await _seed_session(user_id=user_id)
    job_id = await _seed_job(
        user_id=user_id, hash_id="j_curpatchaaab", seq_no=1, session_id=sid
    )
    img = await _seed_image_on_disk(
        job_id=job_id, hash_id="j_curpatchaaab", order=1
    )

    resp = await seeded_app.patch(
        f"/api/sessions/{sid}/cursor",
        headers=_auth(token),
        json={"cursor_image_id": img},
    )
    assert resp.status_code == 200
    assert resp.json()["cursor_image_id"] == img


# ---------------------------------------------------------------------------
# Deck overview
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deck_overview_aggregates_state(
    seeded_app: httpx.AsyncClient,
) -> None:
    token, user_id = await _login_user(seeded_app, username="deck_over")

    s_empty = await _seed_session(user_id=user_id, name="empty")
    s_judging = await _seed_session(user_id=user_id, name="judging")
    job_b = await _seed_job(
        user_id=user_id, hash_id="j_deckbaaaaaab", seq_no=1, session_id=s_judging
    )
    await _seed_image_on_disk(
        job_id=job_b, hash_id="j_deckbaaaaaab", order=1,
        starred=True, pick_state="picked",
    )
    await _seed_image_on_disk(
        job_id=job_b, hash_id="j_deckbaaaaaab", order=2,
    )

    from app.db.engine import get_session
    from app.db.models import Session as SessionRow
    from sqlalchemy import update as sql_update

    async with get_session() as session:
        await session.execute(
            sql_update(SessionRow)
            .where(SessionRow.id == s_judging)
            .values(picker_state="judging")
        )

    resp = await seeded_app.get(
        "/api/picker/overview", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deck_title"]
    assert len(body["sessions"]) == 2
    by_id = {s["id"]: s for s in body["sessions"]}
    assert by_id[s_empty]["picker_state"] == "not_started"
    assert by_id[s_judging]["stats"]["picked"] == 1
    assert by_id[s_judging]["stats"]["unjudged"] == 1
    assert body["totals"]["images"] >= 2
    assert body["summary"]["judging"] >= 1


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_picker_endpoints_require_auth(
    seeded_app: httpx.AsyncClient,
) -> None:
    for path, method in [
        ("/api/picker/overview", "GET"),
        ("/api/sessions/sess_nope/picker", "GET"),
        ("/api/jobs/j_nopenopenop2/images/1/pick", "POST"),
        ("/api/jobs/j_nopenopenop2/images/1/discard", "POST"),
        ("/api/jobs/j_nopenopenop2/images/1/final", "POST"),
        ("/api/jobs/j_nopenopenop2/images/1/defer", "POST"),
        ("/api/jobs/j_nopenopenop2/images/1/unjudge", "POST"),
    ]:
        if method == "GET":
            resp = await seeded_app.get(path)
        else:
            resp = await seeded_app.post(path)
        assert resp.status_code == 401, f"{path}: {resp.status_code}"
