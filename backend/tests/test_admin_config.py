"""End-to-end tests for the admin config + tier routers.

These boot the FastAPI app via lifespan (so the seed and ConfigCenter
hydrate exactly as in production) and hit the public HTTP surface.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _login_admin(client: httpx.AsyncClient) -> str:
    resp = await client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-admin-password"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def _login_user(client: httpx.AsyncClient) -> str:
    """Create a non-admin user, log in, return the token."""
    from app.db.engine import get_session
    from app.db.models import User
    from app.utils.security import hash_password

    async with get_session() as session:
        session.add(
            User(
                id="u_normaluser01",
                username="alice_norm",
                password_hash=hash_password("alicepw1"),
                role="user",
                tier="free",
            )
        )

    resp = await client.post(
        "/api/auth/login",
        json={"username": "alice_norm", "password": "alicepw1"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# GET /api/admin/config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_full_config_returns_all_seeded_keys(
    seeded_app: httpx.AsyncClient,
) -> None:
    from app.domain.config_center import KNOWN_CONFIG_KEYS

    token = await _login_admin(seeded_app)
    resp = await seeded_app.get("/api/admin/config", headers=_auth(token))
    assert resp.status_code == 200

    body = resp.json()
    assert set(body.keys()) == set(KNOWN_CONFIG_KEYS.keys())
    assert body["scheduler.global_max_workers"] == 32
    assert body["emergency.pause_generation"] is False


@pytest.mark.asyncio
async def test_get_config_requires_auth(seeded_app: httpx.AsyncClient) -> None:
    resp = await seeded_app.get("/api/admin/config")
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_get_config_forbidden_for_non_admin(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_user(seeded_app)
    resp = await seeded_app.get("/api/admin/config", headers=_auth(token))
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "FORBIDDEN"


# ---------------------------------------------------------------------------
# GET /api/admin/config/<key>
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_single_key_known_returns_value(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.get(
        "/api/admin/config/scheduler.global_max_workers",
        headers=_auth(token),
    )
    assert resp.status_code == 200
    assert resp.json() == {"key": "scheduler.global_max_workers", "value": 32}


@pytest.mark.asyncio
async def test_get_single_key_unknown_returns_404(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.get(
        "/api/admin/config/no.such.key", headers=_auth(token)
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# PATCH /api/admin/config — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_config_updates_single_key(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.patch(
        "/api/admin/config",
        headers=_auth(token),
        json={"scheduler.global_max_workers": 64},
    )
    assert resp.status_code == 200
    assert resp.json() == {"updated": {"scheduler.global_max_workers": 64}}

    # The new value is visible on subsequent GETs without restart.
    again = await seeded_app.get(
        "/api/admin/config/scheduler.global_max_workers",
        headers=_auth(token),
    )
    assert again.json()["value"] == 64


@pytest.mark.asyncio
async def test_patch_config_writes_audit_row(
    seeded_app: httpx.AsyncClient,
) -> None:
    """Every config write lands an entry in audit_log."""
    from app.db.engine import get_session
    from app.db.models import AuditLog

    token = await _login_admin(seeded_app)
    await seeded_app.patch(
        "/api/admin/config",
        headers=_auth(token),
        json={"thumbnail.quality": 90},
    )

    async with get_session() as session:
        rows = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "config.update")
            )
        ).scalars().all()

    assert len(rows) == 1
    row = rows[0]
    assert row.actor_user_id  # bootstrap admin id
    assert row.target_kind == "config"
    assert "thumbnail.quality" in (row.payload_json or "")


@pytest.mark.asyncio
async def test_patch_config_runtime_wrappers_see_change(
    seeded_app: httpx.AsyncClient,
) -> None:
    """Changing a key via PATCH is immediately visible to a domain wrapper."""
    from app.domain.runtime_configs import SchedulerConfig

    token = await _login_admin(seeded_app)
    cfg = SchedulerConfig()
    assert cfg.global_max_workers == 32

    resp = await seeded_app.patch(
        "/api/admin/config",
        headers=_auth(token),
        json={"scheduler.global_max_workers": 96},
    )
    assert resp.status_code == 200
    # No reload; the wrapper reads the live ConfigCenter cache.
    assert cfg.global_max_workers == 96


# ---------------------------------------------------------------------------
# PATCH /api/admin/config — validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_config_unknown_key_returns_422(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.patch(
        "/api/admin/config",
        headers=_auth(token),
        json={"definitely.not.real": 1},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "INVALID_PARAMETER"
    assert resp.json()["detail"]["field"] == "definitely.not.real"


@pytest.mark.asyncio
async def test_patch_config_weights_imbalance_returns_422(
    seeded_app: httpx.AsyncClient,
) -> None:
    """Setting cost=0.9 alone breaks the weights-sum-to-1 invariant."""
    token = await _login_admin(seeded_app)
    resp = await seeded_app.patch(
        "/api/admin/config",
        headers=_auth(token),
        json={"provider_scoring.weights.cost": 0.9},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "INVALID_PARAMETER"
    assert resp.json()["detail"]["field"] == "provider_scoring.weights"


@pytest.mark.asyncio
async def test_patch_config_full_weight_rebalance_succeeds(
    seeded_app: httpx.AsyncClient,
) -> None:
    """Submitting all five weights at once (summing to 1.0) is accepted."""
    token = await _login_admin(seeded_app)
    payload = {
        "provider_scoring.weights.cost": 0.40,
        "provider_scoring.weights.success": 0.20,
        "provider_scoring.weights.latency": 0.20,
        "provider_scoring.weights.load": 0.15,
        "provider_scoring.weights.freshness": 0.05,
    }
    resp = await seeded_app.patch(
        "/api/admin/config", headers=_auth(token), json=payload
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_patch_config_empty_body_returns_400(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.patch(
        "/api/admin/config", headers=_auth(token), json={}
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_patch_config_forbidden_for_non_admin(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_user(seeded_app)
    resp = await seeded_app.patch(
        "/api/admin/config",
        headers=_auth(token),
        json={"scheduler.global_max_workers": 64},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_patch_config_atomic_on_validation_error(
    seeded_app: httpx.AsyncClient,
) -> None:
    """One bad key in the batch rejects the whole batch — no partial writes."""
    token = await _login_admin(seeded_app)
    resp = await seeded_app.patch(
        "/api/admin/config",
        headers=_auth(token),
        json={
            "scheduler.global_max_workers": 64,  # valid
            "thumbnail.quality": 9999,  # invalid
        },
    )
    assert resp.status_code == 422

    # Valid key was *not* persisted.
    again = await seeded_app.get(
        "/api/admin/config/scheduler.global_max_workers",
        headers=_auth(token),
    )
    assert again.json()["value"] == 32


# ---------------------------------------------------------------------------
# Tiers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tiers_returns_four_in_canonical_order(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.get("/api/admin/tiers", headers=_auth(token))
    assert resp.status_code == 200

    body = resp.json()["tiers"]
    assert [t["tier"] for t in body] == ["vip", "premium", "standard", "free"]
    assert body[0]["weight"] == 8
    assert body[3]["slo_p95_ms"] is None


@pytest.mark.asyncio
async def test_list_tiers_forbidden_for_non_admin(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_user(seeded_app)
    resp = await seeded_app.get("/api/admin/tiers", headers=_auth(token))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_patch_tier_updates_subset_of_fields(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.patch(
        "/api/admin/tiers/vip",
        headers=_auth(token),
        json={"weight": 16},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["weight"] == 16
    # Other fields untouched.
    assert body["max_concurrency"] == 4
    assert body["soft_quota"] == 100


@pytest.mark.asyncio
async def test_patch_tier_hot_reload_visible_immediately(
    seeded_app: httpx.AsyncClient,
) -> None:
    """After PATCH, ``TierConfig.get('vip').weight`` returns the new value."""
    from app.domain.tier_config import get_tier_config

    token = await _login_admin(seeded_app)
    assert get_tier_config().get("vip").weight == 8
    await seeded_app.patch(
        "/api/admin/tiers/vip",
        headers=_auth(token),
        json={"weight": 12},
    )
    assert get_tier_config().get("vip").weight == 12


@pytest.mark.asyncio
async def test_patch_tier_unknown_returns_404(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.patch(
        "/api/admin/tiers/platinum",
        headers=_auth(token),
        json={"weight": 99},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_patch_tier_rejects_hard_below_soft(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.patch(
        "/api/admin/tiers/vip",
        headers=_auth(token),
        # soft default is 100; setting hard=50 violates hard >= soft.
        json={"hard_quota": 50},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["field"] == "hard_quota"


@pytest.mark.asyncio
async def test_patch_tier_rejects_hard_below_soft_when_both_provided(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.patch(
        "/api/admin/tiers/free",
        headers=_auth(token),
        json={"soft_quota": 100, "hard_quota": 50},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_patch_tier_empty_body_returns_400(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.patch(
        "/api/admin/tiers/vip", headers=_auth(token), json={}
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_patch_tier_writes_audit_row(
    seeded_app: httpx.AsyncClient,
) -> None:
    from app.db.engine import get_session
    from app.db.models import AuditLog

    token = await _login_admin(seeded_app)
    await seeded_app.patch(
        "/api/admin/tiers/vip",
        headers=_auth(token),
        json={"weight": 10},
    )

    async with get_session() as session:
        rows = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "tier.update")
            )
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].target_kind == "tier"
    assert rows[0].target_id == "vip"


@pytest.mark.asyncio
async def test_patch_tier_forbidden_for_non_admin(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_user(seeded_app)
    resp = await seeded_app.patch(
        "/api/admin/tiers/vip",
        headers=_auth(token),
        json={"weight": 16},
    )
    assert resp.status_code == 403
