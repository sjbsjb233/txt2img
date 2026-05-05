"""End-to-end tests for the PR-14 archive sync routes.

Coverage matrix:

- ``GET /api/jobs/<hash>`` — single detail (happy path, unknown id,
  cross-tenant, DELETED filtered out, set summary present, references
  in order).
- ``GET /api/jobs/index`` — full list, ``since=`` delta, ``cursor=``
  pagination, ``session_id=`` filter, DELETED filtered out.
- ``POST /api/jobs/details`` — batch, request ↔ response order, missing
  ids surface as ``not_found:true``.
- ``POST /api/jobs/states`` — batch, position+ETA only for QUEUED.
- ``GET /api/jobs/<hash>/images/<order>/thumb`` — bytes + cache header
  + ETag + 304.
- ``GET /api/jobs/<hash>/images/<order>/original`` — download path.
- ``GET /api/jobs/<hash>/refs/<order>/thumb`` — reference reads.
- ``POST /api/jobs/<hash>/images/<order>/star`` — toggle + explicit value.
- Auth gating on every endpoint.

Helpers seed jobs / images / references directly in the DB so the tests
don't depend on the executor (which would force us to bring up the
provider mock stack as well).
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest


# ---------------------------------------------------------------------------
# Login helpers (mirror test_jobs_api.py)
# ---------------------------------------------------------------------------


async def _login_user(
    client: httpx.AsyncClient,
    *,
    username: str = "alice",
    tier: str = "premium",
) -> tuple[str, str]:
    """Create a user + login. Returns ``(access_token, user_id)``."""
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
# Direct-DB seeding — no executor / no upstream
# ---------------------------------------------------------------------------


async def _seed_job(
    *,
    user_id: str,
    hash_id: str,
    seq_no: int,
    status: str = "SUCCEEDED",
    set_id: str | None = None,
    session_id: str | None = None,
    model: str = "gpt-image-2",
    prompt: str = "a still life",
    params: dict[str, Any] | None = None,
    queued_minutes_ago: int = 5,
    started_minutes_ago: int = 4,
    finished_minutes_ago: int = 3,
    updated_minutes_ago: int = 3,
    tier_at_submit: str = "premium",
) -> str:
    """Insert one job row and return its internal job_id.

    Times are derived from ``*_minutes_ago`` so the relative ordering
    used by ``/jobs/index`` (descending updated_at) is predictable.
    """
    from app.db.engine import get_session
    from app.db.models import Job
    from app.utils.ids import new_job_internal_id

    now = datetime.now(timezone.utc)
    queued_at = now - timedelta(minutes=queued_minutes_ago)
    started_at = (
        now - timedelta(minutes=started_minutes_ago)
        if status in ("RUNNING", "SUCCEEDED", "FAILED")
        else None
    )
    finished_at = (
        now - timedelta(minutes=finished_minutes_ago)
        if status in ("SUCCEEDED", "FAILED", "CANCELLED")
        else None
    )
    updated_at = now - timedelta(minutes=updated_minutes_ago)
    dispatched_at = (
        started_at if started_at is not None else None
    )

    job_id = new_job_internal_id()
    serialised_params = (params or {"prompt": prompt, "model": model}).copy()
    serialised_params.setdefault("prompt", prompt)
    serialised_params.setdefault("model", model)

    import json

    async with get_session() as session:
        session.add(
            Job(
                id=job_id,
                hash_id=hash_id,
                user_id=user_id,
                tier_at_submit=tier_at_submit,
                seq_no=seq_no,
                set_id=set_id,
                session_id=session_id,
                model=model,
                params_json=json.dumps(serialised_params),
                flags_json="{}",
                client_request_id=None,
                status=status,
                status_reason=None,
                provider_used="oai",
                retries=0,
                cost_cny=0.0,
                created_at=queued_at,
                queued_at=queued_at,
                dispatched_at=dispatched_at,
                started_at=started_at,
                finished_at=finished_at,
                updated_at=updated_at,
            )
        )
    return job_id


async def _seed_image_on_disk(
    *,
    job_id: str,
    hash_id: str,
    order: int,
    width: int = 32,
    height: int = 32,
    starred: bool = False,
) -> str:
    """Write a real PNG + WebP thumbnail to ``data/jobs/<hash>/...``.

    Returns the image_id. Uses :mod:`app.services.image_io` so the
    on-disk layout matches what the executor would produce.
    """
    from PIL import Image as PILImage

    from app.config import get_settings
    from app.db.engine import get_session
    from app.db.models import Image
    from app.services import image_io
    from app.utils.ids import new_image_id

    image_io.ensure_job_dirs(hash_id)
    buf = io.BytesIO()
    PILImage.new("RGB", (width, height), color=(123, 200, 80)).save(
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
                width=width,
                height=height,
                format="png",
                file_size_bytes=abs_orig.stat().st_size,
                starred=1 if starred else 0,
            )
        )
    return image_id


async def _seed_reference_on_disk(
    *,
    job_id: str,
    hash_id: str,
    order: int,
    filename: str = "ref.png",
) -> None:
    """Write a tiny reference image via image_io + insert the join row."""
    from PIL import Image as PILImage

    from app.db.engine import get_session
    from app.db.models import JobReference
    from app.services import image_io

    image_io.ensure_job_dirs(hash_id)
    buf = io.BytesIO()
    PILImage.new("RGB", (8, 8), color=(50, 50, 50)).save(buf, format="PNG")
    rel = image_io.save_reference(
        hash_id, order, filename, "image/png", buf.getvalue()
    )
    async with get_session() as session:
        session.add(
            JobReference(
                job_id=job_id,
                ref_order=order,
                filename=filename,
                mime="image/png",
                rel_path=rel,
            )
        )


# ---------------------------------------------------------------------------
# /api/jobs/<hash>
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_job_detail_happy_path(
    seeded_app: httpx.AsyncClient,
) -> None:
    token, user_id = await _login_user(seeded_app)
    job_id = await _seed_job(
        user_id=user_id,
        hash_id="j_aaaaaaaaaaaa",
        seq_no=1,
        status="SUCCEEDED",
    )
    await _seed_image_on_disk(job_id=job_id, hash_id="j_aaaaaaaaaaaa", order=1)

    resp = await seeded_app.get(
        "/api/jobs/j_aaaaaaaaaaaa", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["hash_id"] == "j_aaaaaaaaaaaa"
    assert body["status"] == "SUCCEEDED"
    assert body["model_display_name"] == "ChatGPT Images 2.0"
    assert body["prompt"] == "a still life"
    assert body["set"] is None
    assert len(body["images"]) == 1
    img = body["images"][0]
    assert img["order"] == 1
    assert img["thumb_url"].endswith("/api/jobs/j_aaaaaaaaaaaa/images/1/thumb")
    assert img["download_url"].endswith(
        "/api/jobs/j_aaaaaaaaaaaa/images/1/original"
    )
    assert "provider_used" not in body
    assert "cost_cny" not in body
    assert "retries" not in body
    # Timing.queue_seconds + render_seconds are populated.
    assert body["timing"]["queue_seconds"] is not None
    assert body["timing"]["render_seconds"] is not None


@pytest.mark.asyncio
async def test_get_job_detail_cross_tenant_returns_404(
    seeded_app: httpx.AsyncClient,
) -> None:
    _, owner_id = await _login_user(seeded_app, username="owner_detail")
    await _seed_job(
        user_id=owner_id, hash_id="j_otheruser01", seq_no=1
    )

    other_token, _ = await _login_user(seeded_app, username="thief_detail")
    resp = await seeded_app.get(
        "/api/jobs/j_otheruser01", headers=_auth(other_token)
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_job_detail_filters_deleted(
    seeded_app: httpx.AsyncClient,
) -> None:
    token, user_id = await _login_user(seeded_app, username="deleted_user")
    await _seed_job(
        user_id=user_id, hash_id="j_deleted00001", seq_no=1, status="DELETED"
    )
    resp = await seeded_app.get(
        "/api/jobs/j_deleted00001", headers=_auth(token)
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_job_detail_includes_set_summary(
    seeded_app: httpx.AsyncClient,
) -> None:
    token, user_id = await _login_user(seeded_app, username="set_user")
    job_id = await _seed_job(
        user_id=user_id,
        hash_id="j_setjob001234",
        seq_no=1,
        set_id="set_aaaa111122",
        status="SUCCEEDED",
    )
    # Same set, distinct job (n>1 produces multiple jobs in a set or a
    # single job with multiple images — either way the count comes from
    # the images joined through set_id).
    job_id2 = await _seed_job(
        user_id=user_id,
        hash_id="j_setjob005678",
        seq_no=2,
        set_id="set_aaaa111122",
        status="SUCCEEDED",
    )
    await _seed_image_on_disk(job_id=job_id, hash_id="j_setjob001234", order=1)
    await _seed_image_on_disk(job_id=job_id2, hash_id="j_setjob005678", order=1)

    resp = await seeded_app.get(
        "/api/jobs/j_setjob001234", headers=_auth(token)
    )
    body = resp.json()
    assert body["set"]["set_id"] == "set_aaaa111122"
    assert body["set"]["image_count"] == 2


@pytest.mark.asyncio
async def test_get_job_detail_references_in_order(
    seeded_app: httpx.AsyncClient,
) -> None:
    token, user_id = await _login_user(seeded_app, username="refdetail")
    job_id = await _seed_job(
        user_id=user_id, hash_id="j_refsorder001", seq_no=1
    )
    await _seed_reference_on_disk(
        job_id=job_id, hash_id="j_refsorder001", order=1, filename="banana.png"
    )
    await _seed_reference_on_disk(
        job_id=job_id, hash_id="j_refsorder001", order=2, filename="leaf.png"
    )

    resp = await seeded_app.get(
        "/api/jobs/j_refsorder001", headers=_auth(token)
    )
    body = resp.json()
    assert [r["order"] for r in body["references"]] == [1, 2]
    assert body["references"][0]["filename"] == "banana.png"
    assert body["references"][0]["thumb_url"].endswith(
        "/api/jobs/j_refsorder001/refs/1/thumb"
    )


# ---------------------------------------------------------------------------
# /api/jobs/index
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_jobs_index_empty(
    seeded_app: httpx.AsyncClient,
) -> None:
    token, _ = await _login_user(seeded_app, username="empty_idx")
    resp = await seeded_app.get("/api/jobs/index", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"items": [], "next_cursor": None}


@pytest.mark.asyncio
async def test_jobs_index_returns_only_own_rows_sorted_desc(
    seeded_app: httpx.AsyncClient,
) -> None:
    token, user_id = await _login_user(seeded_app, username="owner_idx")
    # Three rows with descending updated_at (1m / 2m / 3m ago).
    await _seed_job(
        user_id=user_id, hash_id="j_idxrow000001", seq_no=1, updated_minutes_ago=3
    )
    await _seed_job(
        user_id=user_id, hash_id="j_idxrow000002", seq_no=2, updated_minutes_ago=2
    )
    await _seed_job(
        user_id=user_id, hash_id="j_idxrow000003", seq_no=3, updated_minutes_ago=1
    )

    # Another user — must not show up.
    _, intruder_id = await _login_user(seeded_app, username="intruder_idx")
    await _seed_job(
        user_id=intruder_id, hash_id="j_intrurow0001", seq_no=1
    )

    resp = await seeded_app.get("/api/jobs/index", headers=_auth(token))
    body = resp.json()
    assert [it["hash_id"] for it in body["items"]] == [
        "j_idxrow000003",
        "j_idxrow000002",
        "j_idxrow000001",
    ]
    assert body["next_cursor"] is None


@pytest.mark.asyncio
async def test_jobs_index_filters_deleted_status(
    seeded_app: httpx.AsyncClient,
) -> None:
    token, user_id = await _login_user(seeded_app, username="del_idx")
    await _seed_job(
        user_id=user_id, hash_id="j_visidxalive1", seq_no=1, status="SUCCEEDED"
    )
    await _seed_job(
        user_id=user_id, hash_id="j_visidxghost1", seq_no=2, status="DELETED"
    )
    resp = await seeded_app.get("/api/jobs/index", headers=_auth(token))
    body = resp.json()
    assert [it["hash_id"] for it in body["items"]] == ["j_visidxalive1"]


@pytest.mark.asyncio
async def test_jobs_index_since_returns_only_recent(
    seeded_app: httpx.AsyncClient,
) -> None:
    token, user_id = await _login_user(seeded_app, username="since_idx")
    await _seed_job(
        user_id=user_id, hash_id="j_idxold0000a1", seq_no=1, updated_minutes_ago=120
    )
    await _seed_job(
        user_id=user_id, hash_id="j_idxnew0000b2", seq_no=2, updated_minutes_ago=2
    )
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    resp = await seeded_app.get(
        "/api/jobs/index",
        params={"since": cutoff},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [it["hash_id"] for it in body["items"]] == ["j_idxnew0000b2"]


@pytest.mark.asyncio
async def test_jobs_index_with_session_filter(
    seeded_app: httpx.AsyncClient,
) -> None:
    token, user_id = await _login_user(seeded_app, username="sess_idx")
    sess_resp = await seeded_app.post(
        "/api/sessions", headers=_auth(token), json={"name": "Editorial"}
    )
    sess_id = sess_resp.json()["id"]
    await _seed_job(
        user_id=user_id,
        hash_id="j_sessbound001",
        seq_no=1,
        session_id=sess_id,
    )
    await _seed_job(
        user_id=user_id, hash_id="j_unbound00001", seq_no=2
    )
    resp = await seeded_app.get(
        f"/api/jobs/index?session_id={sess_id}", headers=_auth(token)
    )
    body = resp.json()
    assert [it["hash_id"] for it in body["items"]] == ["j_sessbound001"]


@pytest.mark.asyncio
async def test_jobs_index_pagination_cursor(
    seeded_app: httpx.AsyncClient,
) -> None:
    token, user_id = await _login_user(seeded_app, username="cur_idx")
    # Five rows; ask for limit=2 and walk three pages.
    for i in range(5):
        await _seed_job(
            user_id=user_id,
            hash_id=f"j_cur{i:09d}1",
            seq_no=i + 1,
            updated_minutes_ago=10 - i,
        )
    page1 = await seeded_app.get(
        "/api/jobs/index?limit=2", headers=_auth(token)
    )
    body1 = page1.json()
    assert len(body1["items"]) == 2
    assert body1["next_cursor"] is not None

    page2 = await seeded_app.get(
        f"/api/jobs/index?limit=2&cursor={body1['next_cursor']}",
        headers=_auth(token),
    )
    body2 = page2.json()
    assert len(body2["items"]) == 2
    # No overlap between page1 and page2.
    page1_ids = {it["hash_id"] for it in body1["items"]}
    page2_ids = {it["hash_id"] for it in body2["items"]}
    assert page1_ids.isdisjoint(page2_ids)

    page3 = await seeded_app.get(
        f"/api/jobs/index?limit=2&cursor={body2['next_cursor']}",
        headers=_auth(token),
    )
    body3 = page3.json()
    assert len(body3["items"]) == 1
    assert body3["next_cursor"] is None


# ---------------------------------------------------------------------------
# POST /api/jobs/details
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_jobs_details_aligns_request_order(
    seeded_app: httpx.AsyncClient,
) -> None:
    token, user_id = await _login_user(seeded_app, username="det_user")
    await _seed_job(user_id=user_id, hash_id="j_detexists001", seq_no=1)
    await _seed_job(user_id=user_id, hash_id="j_detexists002", seq_no=2)

    resp = await seeded_app.post(
        "/api/jobs/details",
        headers=_auth(token),
        json={
            "hash_ids": [
                "j_detexists002",
                "j_detmissing00",  # not seeded
                "j_detexists001",
            ]
        },
    )
    body = resp.json()
    assert resp.status_code == 200, resp.text
    assert len(body["items"]) == 3
    assert body["items"][0]["hash_id"] == "j_detexists002"
    assert body["items"][1]["hash_id"] == "j_detmissing00"
    assert body["items"][1]["not_found"] is True
    assert body["items"][2]["hash_id"] == "j_detexists001"


@pytest.mark.asyncio
async def test_post_jobs_details_too_many_returns_422(
    seeded_app: httpx.AsyncClient,
) -> None:
    token, _ = await _login_user(seeded_app, username="det_too_many")
    resp = await seeded_app.post(
        "/api/jobs/details",
        headers=_auth(token),
        json={"hash_ids": [f"j_id{i:09d}11" for i in range(60)]},
    )
    # 51 ids exceeds the schema's 50-cap.
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_post_jobs_details_other_users_jobs_are_not_found(
    seeded_app: httpx.AsyncClient,
) -> None:
    """A foreign hash_id must not leak; surface as ``not_found:true``."""
    _, owner_id = await _login_user(seeded_app, username="det_owner")
    await _seed_job(user_id=owner_id, hash_id="j_detforeign01", seq_no=1)

    token, _ = await _login_user(seeded_app, username="det_intruder")
    resp = await seeded_app.post(
        "/api/jobs/details",
        headers=_auth(token),
        json={"hash_ids": ["j_detforeign01"]},
    )
    body = resp.json()
    assert body["items"][0]["not_found"] is True


# ---------------------------------------------------------------------------
# POST /api/jobs/states
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_jobs_states_population(
    seeded_app: httpx.AsyncClient,
) -> None:
    token, user_id = await _login_user(seeded_app, username="state_user")
    await _seed_job(
        user_id=user_id, hash_id="j_statequeue01", seq_no=1, status="QUEUED"
    )
    await _seed_job(
        user_id=user_id,
        hash_id="j_staterunning",
        seq_no=2,
        status="RUNNING",
    )
    await _seed_job(
        user_id=user_id, hash_id="j_statedone001", seq_no=3, status="SUCCEEDED"
    )

    resp = await seeded_app.post(
        "/api/jobs/states",
        headers=_auth(token),
        json={
            "hash_ids": [
                "j_statequeue01",
                "j_staterunning",
                "j_statedone001",
                "j_stateMisses1",
            ]
        },
    )
    body = resp.json()
    assert resp.status_code == 200, resp.text
    by_hash = {it["hash_id"]: it for it in body["items"]}

    assert by_hash["j_statequeue01"]["status"] == "QUEUED"
    # Position is None because the job wasn't enqueued in the in-memory
    # queue (we seeded straight into the DB). The endpoint still returns
    # an entry so the client knows the row is QUEUED.
    assert "position" in by_hash["j_statequeue01"]
    assert by_hash["j_staterunning"]["status"] == "RUNNING"
    assert by_hash["j_staterunning"]["position"] is None
    assert by_hash["j_statedone001"]["status"] == "SUCCEEDED"
    assert by_hash["j_statedone001"]["position"] is None
    assert by_hash["j_stateMisses1"]["not_found"] is True


@pytest.mark.asyncio
async def test_post_jobs_states_too_many(
    seeded_app: httpx.AsyncClient,
) -> None:
    token, _ = await _login_user(seeded_app, username="state_overflow")
    resp = await seeded_app.post(
        "/api/jobs/states",
        headers=_auth(token),
        json={"hash_ids": [f"j_st{i:09d}11" for i in range(101)]},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Image / reference reads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_image_thumb_serves_webp_and_etag(
    seeded_app: httpx.AsyncClient,
) -> None:
    token, user_id = await _login_user(seeded_app, username="thumb_user")
    job_id = await _seed_job(
        user_id=user_id, hash_id="j_thumb0000001", seq_no=1
    )
    await _seed_image_on_disk(
        job_id=job_id, hash_id="j_thumb0000001", order=1
    )

    first = await seeded_app.get(
        "/api/jobs/j_thumb0000001/images/1/thumb",
        headers=_auth(token),
    )
    assert first.status_code == 200, first.text
    assert first.headers["content-type"] == "image/webp"
    assert "max-age=2592000" in first.headers["cache-control"]
    etag = first.headers.get("etag")
    assert etag

    # If-None-Match revalidate → 304.
    second = await seeded_app.get(
        "/api/jobs/j_thumb0000001/images/1/thumb",
        headers={**_auth(token), "If-None-Match": etag},
    )
    assert second.status_code == 304


@pytest.mark.asyncio
async def test_get_image_original_streams_attachment(
    seeded_app: httpx.AsyncClient,
) -> None:
    token, user_id = await _login_user(seeded_app, username="orig_user")
    job_id = await _seed_job(
        user_id=user_id, hash_id="j_orig00000001", seq_no=1
    )
    await _seed_image_on_disk(
        job_id=job_id, hash_id="j_orig00000001", order=1
    )

    resp = await seeded_app.get(
        "/api/jobs/j_orig00000001/images/1/original",
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert "attachment" in resp.headers.get("content-disposition", "")
    assert "no-store" in resp.headers.get("cache-control", "")
    assert resp.headers["content-type"] == "image/png"


@pytest.mark.asyncio
async def test_get_image_thumb_cross_tenant_returns_404(
    seeded_app: httpx.AsyncClient,
) -> None:
    _, owner_id = await _login_user(seeded_app, username="thumb_owner")
    job_id = await _seed_job(
        user_id=owner_id, hash_id="j_thumbothera1", seq_no=1
    )
    await _seed_image_on_disk(
        job_id=job_id, hash_id="j_thumbothera1", order=1
    )

    other_token, _ = await _login_user(seeded_app, username="thumb_thief")
    resp = await seeded_app.get(
        "/api/jobs/j_thumbothera1/images/1/thumb",
        headers=_auth(other_token),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_admin_can_stream_other_users_image_thumb(
    seeded_app: httpx.AsyncClient,
) -> None:
    """The admin JobInspector hits the user-scoped /thumb URL and must
    succeed cross-tenant; otherwise the inline image preview is broken
    when admins view another user's recent jobs."""
    _, owner_id = await _login_user(seeded_app, username="admin_thumb_owner")
    job_id = await _seed_job(
        user_id=owner_id, hash_id="j_adminthumb01", seq_no=1
    )
    await _seed_image_on_disk(
        job_id=job_id, hash_id="j_adminthumb01", order=1
    )

    admin_login = await seeded_app.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-admin-password"},
    )
    assert admin_login.status_code == 200, admin_login.text
    admin_token = admin_login.json()["access_token"]

    resp = await seeded_app.get(
        "/api/jobs/j_adminthumb01/images/1/thumb",
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/webp"

    resp_orig = await seeded_app.get(
        "/api/jobs/j_adminthumb01/images/1/original",
        headers=_auth(admin_token),
    )
    assert resp_orig.status_code == 200


@pytest.mark.asyncio
async def test_get_reference_thumb(
    seeded_app: httpx.AsyncClient,
) -> None:
    token, user_id = await _login_user(seeded_app, username="ref_user")
    job_id = await _seed_job(
        user_id=user_id, hash_id="j_refthumb0001", seq_no=1
    )
    await _seed_reference_on_disk(
        job_id=job_id,
        hash_id="j_refthumb0001",
        order=1,
        filename="rocket.png",
    )

    resp = await seeded_app.get(
        "/api/jobs/j_refthumb0001/refs/1/thumb",
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "image/png"
    assert "max-age=2592000" in resp.headers.get("cache-control", "")


# ---------------------------------------------------------------------------
# Star toggle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_star_toggle_default_flips_value(
    seeded_app: httpx.AsyncClient,
) -> None:
    token, user_id = await _login_user(seeded_app, username="star_user")
    job_id = await _seed_job(
        user_id=user_id, hash_id="j_stardflt001a", seq_no=1
    )
    await _seed_image_on_disk(
        job_id=job_id, hash_id="j_stardflt001a", order=1, starred=False
    )

    resp = await seeded_app.post(
        "/api/jobs/j_stardflt001a/images/1/star",
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["starred"] is True

    again = await seeded_app.post(
        "/api/jobs/j_stardflt001a/images/1/star",
        headers=_auth(token),
    )
    assert again.json()["starred"] is False


@pytest.mark.asyncio
async def test_star_toggle_explicit_value(
    seeded_app: httpx.AsyncClient,
) -> None:
    token, user_id = await _login_user(seeded_app, username="star_explicit")
    job_id = await _seed_job(
        user_id=user_id, hash_id="j_starexpl001b", seq_no=1
    )
    await _seed_image_on_disk(
        job_id=job_id, hash_id="j_starexpl001b", order=1, starred=False
    )

    resp = await seeded_app.post(
        "/api/jobs/j_starexpl001b/images/1/star",
        headers=_auth(token),
        json={"starred": True},
    )
    assert resp.json()["starred"] is True

    # Setting True again is idempotent.
    resp = await seeded_app.post(
        "/api/jobs/j_starexpl001b/images/1/star",
        headers=_auth(token),
        json={"starred": True},
    )
    assert resp.json()["starred"] is True


# ---------------------------------------------------------------------------
# Auth gating on every endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archive_endpoints_require_auth(
    seeded_app: httpx.AsyncClient,
) -> None:
    for resp in (
        await seeded_app.get("/api/jobs/j_aaaaaaaaaaaa"),
        await seeded_app.get("/api/jobs/index"),
        await seeded_app.post(
            "/api/jobs/details", json={"hash_ids": ["j_aaaaaaaaaaaa"]}
        ),
        await seeded_app.post(
            "/api/jobs/states", json={"hash_ids": ["j_aaaaaaaaaaaa"]}
        ),
        await seeded_app.get("/api/jobs/j_aaaaaaaaaaaa/images/1/thumb"),
        await seeded_app.get("/api/jobs/j_aaaaaaaaaaaa/images/1/original"),
        await seeded_app.get("/api/jobs/j_aaaaaaaaaaaa/refs/1/thumb"),
        await seeded_app.post("/api/jobs/j_aaaaaaaaaaaa/images/1/star"),
    ):
        assert resp.status_code == 401, resp.text
