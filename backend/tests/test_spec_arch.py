"""T-ARCH-NN spec cases (文生图平台测试方案 §5.12)."""

from __future__ import annotations

import json

import httpx
import pytest

from tests.infra.fake_adapter import FakeAdapter, register_fake_adapter
from tests.infra.jobs import insert_terminal_job
from tests.infra.seeds import auth, install_fake_provider, login_user


pytestmark = [pytest.mark.arch]


# ---------------------------------------------------------------------------
# T-ARCH-01 · /api/jobs/index returns only the user's own jobs
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_arch_01_user_isolation(seeded_app: httpx.AsyncClient):
    A, ta = await login_user(seeded_app, tier="vip", username="arA")
    B, tb = await login_user(seeded_app, tier="vip", username="arB")
    h_a = await insert_terminal_job(A.id, tier="vip", seq_no=1)
    h_b = await insert_terminal_job(B.id, tier="vip", seq_no=1)

    r = await seeded_app.get("/api/jobs/index", headers=auth(ta))
    assert r.status_code == 200, r.text
    body = r.json()
    items = body.get("items") or body.get("jobs") or []
    hashes = {j["hash_id"] for j in items}
    assert h_a in hashes, f"items={items}"
    assert h_b not in hashes


# ---------------------------------------------------------------------------
# T-ARCH-03 · /api/jobs/details capped at 50
# ---------------------------------------------------------------------------
@pytest.mark.p1
async def test_t_arch_03_details_batch_cap(seeded_app: httpx.AsyncClient):
    user, token = await login_user(seeded_app, tier="vip")
    body = {"hash_ids": [f"j_{i:012d}" for i in range(51)]}
    r = await seeded_app.post(
        "/api/jobs/details", headers=auth(token), json=body
    )
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# T-ARCH-04 · unknown hash_id → not_found marker
# ---------------------------------------------------------------------------
@pytest.mark.p1
async def test_t_arch_04_not_found_marker(seeded_app: httpx.AsyncClient):
    user, token = await login_user(seeded_app, tier="vip")
    r = await seeded_app.post(
        "/api/jobs/details",
        headers=auth(token),
        json={"hash_ids": ["j_aaaaaaaaaaaa"]},
    )
    assert r.status_code == 200, r.text
    items = r.json().get("jobs") or r.json().get("items") or []
    assert items
    assert items[0].get("not_found") is True


# ---------------------------------------------------------------------------
# T-ARCH-06 · path traversal in hash_id → 400/422 (no file read)
# ---------------------------------------------------------------------------
@pytest.mark.p0
@pytest.mark.err
async def test_t_arch_06_path_traversal_rejected(seeded_app: httpx.AsyncClient):
    user, token = await login_user(seeded_app, tier="vip")
    bad = "..%2Fetc%2Fpasswd"
    r = await seeded_app.get(
        f"/api/jobs/{bad}/images/1/original", headers=auth(token)
    )
    assert r.status_code in (400, 404, 422)


# ---------------------------------------------------------------------------
# T-ARCH-07 · cross-tenant detail → 404 (not 403, no existence leak)
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_arch_07_cross_tenant_404(seeded_app: httpx.AsyncClient):
    A, ta = await login_user(seeded_app, tier="vip", username="cA")
    B, tb = await login_user(seeded_app, tier="vip", username="cB")
    h = await insert_terminal_job(A.id, tier="vip", seq_no=1)
    r = await seeded_app.get(f"/api/jobs/{h}", headers=auth(tb))
    assert r.status_code == 404
