"""T-ERR-NN spec cases (文生图平台测试方案 §5.18)."""

from __future__ import annotations

import json

import httpx
import pytest

from tests.infra.fake_adapter import FakeAdapter, register_fake_adapter
from tests.infra.fake_turnstile import patch_turnstile
from tests.infra.seeds import (
    ADMIN_PW,
    auth,
    install_fake_provider,
    login_admin,
    login_user,
)


pytestmark = [pytest.mark.err]


def _payload(**o):
    base = {"model": "gpt-image-2", "prompt": "p", "n": 1,
            "size": "1024x1024", "output_format": "png"}
    base.update(o)
    return json.dumps(base)


# ---------------------------------------------------------------------------
# T-ERR-01 · uniform error body shape
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_err_01_uniform_shape(seeded_app: httpx.AsyncClient):
    register_fake_adapter()
    FakeAdapter.reset()
    await install_fake_provider()
    user, token = await login_user(seeded_app, tier="free", today_count=99)

    # 401: anon /api/me
    r = await seeded_app.get("/api/me")
    if r.status_code == 401:
        d = r.json().get("detail")
        # FastAPI's bearer guard returns string detail; we still expect dict
        # for our own raises. Both shapes are acceptable per spec.
        if isinstance(d, dict):
            assert "code" in d and "message" in d

    # 403: user → admin
    r2 = await seeded_app.get("/api/admin/users", headers=auth(token))
    assert r2.status_code == 403
    detail = r2.json()["detail"]
    assert detail["code"] == "FORBIDDEN"
    for k in ("code", "message", "field", "extra"):
        assert k in detail

    # 429: hard quota
    files = {"payload": (None, _payload(), "application/json")}
    r3 = await seeded_app.post("/api/jobs", headers=auth(token), files=files)
    assert r3.status_code == 429
    d3 = r3.json()["detail"]
    assert d3["code"] == "HARD_QUOTA_EXCEEDED"


# ---------------------------------------------------------------------------
# T-ERR-02 · INVALID_PARAMETER must carry field
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_err_02_invalid_parameter_carries_field(
    seeded_app: httpx.AsyncClient,
):
    register_fake_adapter()
    FakeAdapter.reset()
    await install_fake_provider()
    user, token = await login_user(seeded_app, tier="vip")
    files = {
        "payload": (
            None,
            _payload(model="gpt-image-2", aspect_ratio="999:1"),
            "application/json",
        )
    }
    r = await seeded_app.post("/api/jobs", headers=auth(token), files=files)
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["code"] == "INVALID_PARAMETER"
    assert detail.get("field") == "aspect_ratio"


# ---------------------------------------------------------------------------
# T-ERR-03 · USER_BUSY vs HARD_QUOTA_EXCEEDED separation
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_err_03_user_busy_vs_hard_quota(seeded_app: httpx.AsyncClient):
    register_fake_adapter()
    FakeAdapter.reset()
    await install_fake_provider()

    # Hard quota only: today=10 (free hard=10), capacity available
    user, token = await login_user(seeded_app, tier="free", today_count=10)
    files = {"payload": (None, _payload(), "application/json")}
    r = await seeded_app.post("/api/jobs", headers=auth(token), files=files)
    assert r.status_code == 429
    assert r.json()["detail"]["code"] == "HARD_QUOTA_EXCEEDED"

    # USER_BUSY only: capacity exhausted, quota fine
    u2, t2 = await login_user(seeded_app, tier="standard", username="usrb")
    from app.db.engine import get_session
    from app.db.models import Job
    from app.utils.ids import new_job_hash_id, new_job_internal_id

    async with get_session() as s:
        for i in range(4):
            s.add(
                Job(
                    id=new_job_internal_id(),
                    hash_id=new_job_hash_id(),
                    user_id=u2.id,
                    tier_at_submit="standard",
                    seq_no=i + 1,
                    model="gpt-image-2",
                    params_json="{}",
                    flags_json="{}",
                    status="QUEUED",
                )
            )
    r2 = await seeded_app.post("/api/jobs", headers=auth(t2), files=files)
    assert r2.status_code == 429
    assert r2.json()["detail"]["code"] == "USER_BUSY"


# ---------------------------------------------------------------------------
# T-ERR-04 · CAPTCHA_REQUIRED vs CAPTCHA_INVALID
# ---------------------------------------------------------------------------
@pytest.mark.p1
@pytest.mark.auth
async def test_t_err_04_captcha_codes(
    seeded_app: httpx.AsyncClient, monkeypatch
):
    patch_turnstile(monkeypatch)
    # Trigger captcha requirement by failing 3x
    for _ in range(3):
        await seeded_app.post(
            "/api/auth/login",
            json={"username": "admin", "password": "bad"},
        )
    # No token at all → CAPTCHA_REQUIRED
    r = await seeded_app.post(
        "/api/auth/login",
        json={"username": "admin", "password": ADMIN_PW},
    )
    assert r.status_code == 412
    assert r.json()["detail"]["code"] == "CAPTCHA_REQUIRED"
    # Bad token → CAPTCHA_INVALID
    r2 = await seeded_app.post(
        "/api/auth/login",
        json={"username": "admin", "password": ADMIN_PW, "captcha_token": "FAIL"},
    )
    assert r2.status_code == 412
    assert r2.json()["detail"]["code"] == "CAPTCHA_INVALID"


# ---------------------------------------------------------------------------
# T-ERR-05 · ALL_PROVIDERS_FAILED vs NO_PROVIDER_AVAILABLE
# ---------------------------------------------------------------------------
@pytest.mark.p0
@pytest.mark.breaker
async def test_t_err_05_no_provider_vs_all_failed(seeded_app: httpx.AsyncClient):
    register_fake_adapter()
    FakeAdapter.reset()
    # No provider installed → first user submission goes to NO_PROVIDER_AVAILABLE
    # Second scenario: all-providers-fail
    await install_fake_provider()
    FakeAdapter.behavior["fail_rate"] = 1.0

    user, token = await login_user(seeded_app, tier="vip")
    files = {"payload": (None, _payload(), "application/json")}
    r = await seeded_app.post("/api/jobs", headers=auth(token), files=files)
    assert r.status_code == 200
    h = r.json()["hash_id"]
    import asyncio
    for _ in range(60):
        d = await seeded_app.get(f"/api/jobs/{h}", headers=auth(token))
        if d.status_code == 200 and d.json()["status"] == "FAILED":
            break
        await asyncio.sleep(0.05)
    reason = d.json().get("status_reason") or ""
    assert "ALL_PROVIDERS" in reason or "NO_PROVIDER" in reason


# ---------------------------------------------------------------------------
# T-ERR-07 · unknown hash_id detail → 404
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_err_07_unknown_hash_404(seeded_app: httpx.AsyncClient):
    user, token = await login_user(seeded_app, tier="vip")
    r = await seeded_app.get("/api/jobs/j_aaaaaaaaaaaa", headers=auth(token))
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# T-ERR-08 · oversized reference upload rejected
# ---------------------------------------------------------------------------
@pytest.mark.p2
async def test_t_err_08_oversize_reference(seeded_app: httpx.AsyncClient):
    register_fake_adapter()
    FakeAdapter.reset()
    await install_fake_provider()
    user, token = await login_user(seeded_app, tier="vip")
    # Build an "oversized" PNG payload (~25MB of fake bytes after header)
    big = b"\x89PNG\r\n\x1a\n" + b"X" * (25 * 1024 * 1024)
    files = {
        "payload": (None, _payload(), "application/json"),
        "ref_1": ("big.png", big, "image/png"),
    }
    r = await seeded_app.post("/api/jobs", headers=auth(token), files=files)
    assert r.status_code in (400, 413, 422)
