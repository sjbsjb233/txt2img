"""T-ANN-NN spec cases (文生图平台测试方案 §5.14)."""

from __future__ import annotations

import httpx
import pytest

from tests.infra.seeds import auth, login_admin, login_user


pytestmark = [pytest.mark.ann]


def _ann_payload(**o):
    from datetime import datetime, timezone
    base = {
        "title": "Hello",
        "content_kind": "text",
        "content": "Welcome to the platform.",
        "audience_kind": "tier",
        "audience_data": ["premium"],
        "starts_at": datetime.now(timezone.utc).isoformat(),
    }
    base.update(o)
    return base


# ---------------------------------------------------------------------------
# T-ANN-01 · audience filter — tier=premium reaches premium, not standard
# ---------------------------------------------------------------------------
@pytest.mark.p0
@pytest.mark.admin
async def test_t_ann_01_audience_tier(seeded_app: httpx.AsyncClient):
    admin = await login_admin(seeded_app)
    A, ta = await login_user(seeded_app, tier="premium", username="annA")
    B, tb = await login_user(seeded_app, tier="standard", username="annB")

    r = await seeded_app.post(
        "/api/admin/announcements", headers=auth(admin), json=_ann_payload()
    )
    assert r.status_code in (200, 201), r.text

    a_active = await seeded_app.get("/api/announcements/active", headers=auth(ta))
    b_active = await seeded_app.get("/api/announcements/active", headers=auth(tb))
    a_titles = [x.get("title") for x in (a_active.json().get("items") or [])]
    b_titles = [x.get("title") for x in (b_active.json().get("items") or [])]
    assert "Hello" in a_titles, a_active.json()
    assert "Hello" not in b_titles


# ---------------------------------------------------------------------------
# T-ANN-03 · read marks dismissed for that user
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_ann_03_mark_read_dismisses(seeded_app: httpx.AsyncClient):
    admin = await login_admin(seeded_app)
    A, ta = await login_user(seeded_app, tier="premium", username="annR")

    # Audience: all tiers
    r = await seeded_app.post(
        "/api/admin/announcements",
        headers=auth(admin),
        json=_ann_payload(audience_kind="all", audience_data=None),
    )
    assert r.status_code in (200, 201), r.text
    ann_id = r.json().get("id") or r.json().get("announcement_id")
    assert ann_id

    active1 = await seeded_app.get("/api/announcements/active", headers=auth(ta))
    assert any(
        a.get("id") == ann_id
        for a in active1.json().get("items", [])
    )

    rd = await seeded_app.post(
        f"/api/announcements/{ann_id}/read", headers=auth(ta)
    )
    assert rd.status_code in (200, 204), rd.text

    active2 = await seeded_app.get("/api/announcements/active", headers=auth(ta))
    ids2 = {a.get("id") for a in active2.json().get("items", [])}
    assert ann_id not in ids2


# ---------------------------------------------------------------------------
# T-ANN-05 · v1 rejects content_kind=react
# ---------------------------------------------------------------------------
@pytest.mark.p1
@pytest.mark.admin
async def test_t_ann_05_react_kind_rejected(seeded_app: httpx.AsyncClient):
    admin = await login_admin(seeded_app)
    r = await seeded_app.post(
        "/api/admin/announcements",
        headers=auth(admin),
        json=_ann_payload(content_kind="react"),
    )
    assert r.status_code in (400, 422)
