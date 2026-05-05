"""T-EMERG-NN spec cases (文生图平台测试方案 §5.15)."""

from __future__ import annotations

import json

import httpx
import pytest

from tests.infra.fake_adapter import FakeAdapter, register_fake_adapter
from tests.infra.seeds import auth, install_fake_provider, login_admin, login_user


pytestmark = [pytest.mark.emerg]


def _payload(**o):
    base = {"model": "gpt-image-2", "prompt": "p", "n": 1,
            "size": "1024x1024", "output_format": "png"}
    base.update(o)
    return json.dumps(base)


async def _set_switch(seeded_app, admin_token, key, value):
    return await seeded_app.patch(
        "/api/admin/config",
        headers=auth(admin_token),
        json={key: value},
    )


# ---------------------------------------------------------------------------
# T-EMERG-01 · pause_generation blocks job creation
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_emerg_01_pause_generation_blocks(seeded_app: httpx.AsyncClient):
    register_fake_adapter()
    FakeAdapter.reset()
    await install_fake_provider()
    admin = await login_admin(seeded_app)
    r = await _set_switch(seeded_app, admin, "emergency.pause_generation", True)
    assert r.status_code == 200, r.text

    user, token = await login_user(seeded_app, tier="vip")
    r2 = await seeded_app.post(
        "/api/jobs",
        headers=auth(token),
        files={"payload": (None, _payload(), "application/json")},
    )
    assert r2.status_code == 403
    assert r2.json()["detail"]["code"] == "BLOCKED_BY_EMERGENCY"


# ---------------------------------------------------------------------------
# T-EMERG-04 · block_new_member_login → user 401, admin still ok
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_emerg_04_block_login(seeded_app: httpx.AsyncClient):
    admin = await login_admin(seeded_app)
    user, _ = await login_user(seeded_app, tier="vip", username="emr04")
    r = await _set_switch(
        seeded_app, admin, "emergency.block_new_member_login", True
    )
    assert r.status_code == 200, r.text

    from tests.infra.seeds import USER_PW

    bad = await seeded_app.post(
        "/api/auth/login",
        json={"username": "emr04", "password": USER_PW},
    )
    assert bad.status_code == 401
    assert bad.json()["detail"]["code"] == "BLOCKED_BY_EMERGENCY"

    # Admin can still log in
    from tests.infra.seeds import ADMIN_PW

    ok = await seeded_app.post(
        "/api/auth/login",
        json={"username": "admin", "password": ADMIN_PW},
    )
    assert ok.status_code == 200
