"""§14 + §16 of the testing doc — auth boundary + error envelope shape."""

from __future__ import annotations

import pytest

from tests._batch_helpers import auth, login_user, register_batch


# ---------------------------------------------------------------------------
# §14 AUTHZ
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_endpoints_require_auth(seeded_app):
    """Doc §14.test_all_endpoints_require_auth — every batch route is gated."""
    pairs = [
        ("GET", "/api/batches"),
        ("POST", "/api/batches"),
        ("GET", "/api/batches/bat_xxxxxxxxxx"),
        ("POST", "/api/batches/bat_xxxxxxxxxx/finalize_submission"),
        ("POST", "/api/batches/bat_xxxxxxxxxx/cancel"),
        ("DELETE", "/api/batches/bat_xxxxxxxxxx"),
    ]
    for method, path in pairs:
        resp = await seeded_app.request(method, path)
        assert resp.status_code == 401, f"{method} {path} expected 401 got {resp.status_code}"


@pytest.mark.asyncio
async def test_cross_user_404(seeded_app):
    """Doc §14.test_cross_user_404 — Bob using Alice's batch_id sees 404."""
    alice = await login_user(seeded_app)
    bob = await login_user(seeded_app, username="bob")
    batch = await register_batch(seeded_app, alice)
    bid = batch["batch_id"]
    paths = [
        ("GET", f"/api/batches/{bid}"),
        ("POST", f"/api/batches/{bid}/finalize_submission"),
        ("POST", f"/api/batches/{bid}/cancel"),
        ("DELETE", f"/api/batches/{bid}"),
    ]
    for method, path in paths:
        resp = await seeded_app.request(method, path, headers=auth(bob))
        assert resp.status_code == 404, f"{method} {path}: {resp.status_code}"


# ---------------------------------------------------------------------------
# §16 ERROR ENVELOPE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_error_response_shape_invalid_param(seeded_app):
    """Doc §16.test_error_response_shape — INVALID_PARAMETER (field=batch_id)."""
    token = await login_user(seeded_app)
    body = {"title": "x", "total_job_count": 1, "spec": {"slots": []}}
    resp = await seeded_app.post("/api/batches", headers=auth(token), json=body)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "code" in detail and "message" in detail and "field" in detail


@pytest.mark.asyncio
async def test_error_response_shape_batch_limit(seeded_app):
    """Doc §16 — BATCH_LIMIT_EXCEEDED carries the right code."""
    token = await login_user(seeded_app)
    for _ in range(3):
        await register_batch(seeded_app, token)
    body = {
        "title": "x",
        "total_job_count": 1,
        "spec": {
            "fixed_prompt_summary": "",
            "fixed_ref_count": 0,
            "session_strategy": "none",
            "shared_session_id": None,
            "slots": [
                {
                    "stable_idx": 1,
                    "title": "s",
                    "prompt_summary": "",
                    "image_count": 1,
                    "set_id": None,
                    "session_id": None,
                }
            ],
        },
    }
    resp = await seeded_app.post("/api/batches", headers=auth(token), json=body)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == "BATCH_LIMIT_EXCEEDED"
