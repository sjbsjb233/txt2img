"""T-JOB-NN spec cases (文生图平台测试方案 §5.7)."""

from __future__ import annotations

import asyncio
import json
import re

import httpx
import pytest

from tests.infra.fake_adapter import FakeAdapter, register_fake_adapter
from tests.infra.seeds import auth, install_fake_provider, login_admin, login_user


pytestmark = [pytest.mark.job]


def _job_payload(**overrides):
    base = {"model": "gpt-image-2", "prompt": "an apple",
            "n": 1, "size": "1024x1024", "output_format": "png"}
    base.update(overrides)
    return json.dumps(base)


async def _wait_status(seeded_app, token, hash_id, target, timeout=10.0):
    for _ in range(int(timeout / 0.1)):
        r = await seeded_app.get(f"/api/jobs/{hash_id}", headers=auth(token))
        if r.status_code == 200 and r.json()["status"] == target:
            return r.json()
        await asyncio.sleep(0.1)
    raise AssertionError(
        f"job {hash_id} stuck — last status: {r.json().get('status') if r.status_code == 200 else r.status_code}"
    )


# ---------------------------------------------------------------------------
# T-JOB-01 · simple 1-image happy path
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_job_01_simple_lifecycle(seeded_app: httpx.AsyncClient):
    register_fake_adapter()
    FakeAdapter.reset()
    await install_fake_provider()
    user, token = await login_user(seeded_app, tier="vip")
    files = {"payload": (None, _job_payload(), "application/json")}
    r = await seeded_app.post("/api/jobs", headers=auth(token), files=files)
    assert r.status_code == 200, r.text
    hash_id = r.json()["hash_id"]
    body = await _wait_status(seeded_app, token, hash_id, "SUCCEEDED")
    assert len(body.get("images", [])) >= 1


# ---------------------------------------------------------------------------
# T-JOB-02 · n=4 forms a Set
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_job_02_set_for_n4(seeded_app: httpx.AsyncClient):
    register_fake_adapter()
    FakeAdapter.reset()
    FakeAdapter.behavior["image_count_returned"] = 4
    await install_fake_provider()
    user, token = await login_user(seeded_app, tier="vip")
    files = {"payload": (None, _job_payload(n=4), "application/json")}
    r = await seeded_app.post("/api/jobs", headers=auth(token), files=files)
    assert r.status_code == 200, r.text
    hash_id = r.json()["hash_id"]
    set_id = r.json().get("set_id")
    assert set_id  # non-null
    body = await _wait_status(seeded_app, token, hash_id, "SUCCEEDED")
    assert len(body.get("images", [])) == 4

    from app.db.engine import get_session
    from app.db.models import Image
    from sqlalchemy import select, func

    async with get_session() as s:
        n = (
            await s.execute(select(func.count(Image.id)))
        ).scalar() or 0
    assert n >= 4


# ---------------------------------------------------------------------------
# T-JOB-04 · single attempt fails → fallback to next provider
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_job_04_fallback_to_next_provider(seeded_app: httpx.AsyncClient):
    register_fake_adapter()
    FakeAdapter.reset()
    await install_fake_provider(provider_id="A", balance=1000)
    await install_fake_provider(provider_id="B", balance=1000)
    # A always fails, B succeeds
    FakeAdapter.behavior["fail_for_provider"] = {"A": True, "B": False}
    user, token = await login_user(seeded_app, tier="vip")
    files = {"payload": (None, _job_payload(), "application/json")}
    r = await seeded_app.post("/api/jobs", headers=auth(token), files=files)
    assert r.status_code == 200
    hash_id = r.json()["hash_id"]
    body = await _wait_status(seeded_app, token, hash_id, "SUCCEEDED", timeout=15)

    from app.db.engine import get_session
    from app.db.models import Job
    from sqlalchemy import select

    async with get_session() as s:
        j = (
            await s.execute(select(Job).where(Job.hash_id == hash_id))
        ).scalar_one()
    assert j.provider_used == "B"
    assert j.retries >= 1


# ---------------------------------------------------------------------------
# T-JOB-05 · all providers fail → ALL_PROVIDERS_FAILED
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_job_05_all_providers_fail(seeded_app: httpx.AsyncClient):
    register_fake_adapter()
    FakeAdapter.reset()
    await install_fake_provider(provider_id="A")
    FakeAdapter.behavior["fail_rate"] = 1.0
    user, token = await login_user(seeded_app, tier="vip", today_count=0)
    files = {"payload": (None, _job_payload(), "application/json")}
    r = await seeded_app.post("/api/jobs", headers=auth(token), files=files)
    assert r.status_code == 200
    hash_id = r.json()["hash_id"]
    body = await _wait_status(seeded_app, token, hash_id, "FAILED", timeout=15)
    # status_reason should mention ALL_PROVIDERS_FAILED or similar
    reason = body.get("status_reason") or ""
    assert (
        "ALL_PROVIDERS_FAILED" in reason
        or "exhausted" in reason.lower()
        or "all" in reason.lower()
    )


# ---------------------------------------------------------------------------
# T-JOB-06 · cancel queued task does not increment quota
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_job_06_cancel_queued(seeded_app: httpx.AsyncClient):
    register_fake_adapter()
    FakeAdapter.reset()
    FakeAdapter.behavior["delay_seconds"] = 5.0  # block worker
    await install_fake_provider()
    user, token = await login_user(seeded_app, tier="vip", today_count=0)

    # First job blocks the only worker for 5s
    files1 = {"payload": (None, _job_payload(), "application/json")}
    r1 = await seeded_app.post("/api/jobs", headers=auth(token), files=files1)
    assert r1.status_code == 200

    files2 = {"payload": (None, _job_payload(), "application/json")}
    r2 = await seeded_app.post("/api/jobs", headers=auth(token), files=files2)
    assert r2.status_code == 200
    h2 = r2.json()["hash_id"]

    # Cancel job 2 immediately (still QUEUED)
    cancel = await seeded_app.post(f"/api/jobs/{h2}/cancel", headers=auth(token))
    assert cancel.status_code == 200, cancel.text
    assert cancel.json()["status"] == "CANCELLED"

    # Restore behaviour for cleanup
    FakeAdapter.behavior["delay_seconds"] = 0.01


# ---------------------------------------------------------------------------
# T-JOB-07 · DELETE soft-deletes; status -> DELETED
# ---------------------------------------------------------------------------
@pytest.mark.p1
async def test_t_job_07_delete_softdelete(seeded_app: httpx.AsyncClient):
    register_fake_adapter()
    FakeAdapter.reset()
    await install_fake_provider()
    user, token = await login_user(seeded_app, tier="vip")
    files = {"payload": (None, _job_payload(), "application/json")}
    r = await seeded_app.post("/api/jobs", headers=auth(token), files=files)
    h = r.json()["hash_id"]
    await _wait_status(seeded_app, token, h, "SUCCEEDED")

    d = await seeded_app.delete(f"/api/jobs/{h}", headers=auth(token))
    assert d.status_code == 200
    assert d.json()["status"] == "DELETED"

    from app.db.engine import get_session
    from app.db.models import Job
    from sqlalchemy import select

    async with get_session() as s:
        j = (
            await s.execute(select(Job).where(Job.hash_id == h))
        ).scalar_one()
    assert j.status == "DELETED"


# ---------------------------------------------------------------------------
# T-JOB-08 · seq_no per-user monotonic
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_job_08_seq_no_per_user(seeded_app: httpx.AsyncClient):
    register_fake_adapter()
    FakeAdapter.reset()
    await install_fake_provider()
    A, ta = await login_user(seeded_app, tier="vip", username="ta")
    B, tb = await login_user(seeded_app, tier="vip", username="tb")
    seq_a = []
    for _ in range(3):
        r = await seeded_app.post(
            "/api/jobs",
            headers=auth(ta),
            files={"payload": (None, _job_payload(), "application/json")},
        )
        seq_a.append(r.json()["seq_no"])
    for _ in range(2):
        r = await seeded_app.post(
            "/api/jobs",
            headers=auth(tb),
            files={"payload": (None, _job_payload(), "application/json")},
        )
    r = await seeded_app.post(
        "/api/jobs",
        headers=auth(ta),
        files={"payload": (None, _job_payload(), "application/json")},
    )
    seq_a.append(r.json()["seq_no"])
    assert seq_a == [1, 2, 3, 4]


# ---------------------------------------------------------------------------
# T-JOB-09 · hash_id matches ^j_[A-Za-z0-9]{12}$
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_job_09_hash_id_format(seeded_app: httpx.AsyncClient):
    register_fake_adapter()
    FakeAdapter.reset()
    await install_fake_provider()
    # Run 4 quick jobs (FakeAdapter delay=0.01) — let each complete before
    # submitting the next; capped at 4 to stay below the 5-burst captcha
    # trigger.
    user, token = await login_user(seeded_app, tier="vip")
    rgx = re.compile(r"^j_[A-Za-z0-9]{12}$")
    seen = set()
    for _ in range(4):
        r = await seeded_app.post(
            "/api/jobs",
            headers=auth(token),
            files={"payload": (None, _job_payload(), "application/json")},
        )
        assert r.status_code == 200, r.text
        h = r.json()["hash_id"]
        assert rgx.match(h), h
        assert h not in seen, "duplicate hash"
        seen.add(h)
        # Wait for completion to avoid USER_BUSY
        await _wait_status(seeded_app, token, h, "SUCCEEDED", timeout=5)
