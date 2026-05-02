"""E2E happy-path: login → create job → wait for SUCCEEDED → archive view.

Walks the sequence design doc §6.1 sketches: a real user logs in, the
backend has a working provider, the user submits one job, the scheduler
picks it up, the stub adapter "succeeds", and the archive route returns
the SUCCEEDED row complete with one image.
"""

from __future__ import annotations

import httpx
import pytest

from .conftest import (
    auth_header,
    job_payload,
    login_user,
    run_scheduler_until_idle,
    seed_provider,
    submit_job,
    wait_for_status,
)


@pytest.mark.asyncio
async def test_full_login_create_succeeded_archive_download(
    seeded_app: httpx.AsyncClient,
    stub_adapter,
) -> None:
    # 1. User registers (we shortcut registration → seeded user) and logs in.
    token = await login_user(seeded_app, username="happy_path")
    assert token

    me = await seeded_app.get("/api/me", headers=auth_header(token))
    assert me.status_code == 200
    body = me.json()
    # Tier and quota must NEVER appear in /api/me — design doc §3.3.
    assert "tier" not in body
    assert "today_count" not in body
    assert body["username"] == "happy_path"

    # 2. Admin seeds a provider that accepts gpt-image-2 for every tier.
    await seed_provider(
        seeded_app, provider_id="oai_main", label="OpenAI Main"
    )

    # 3. /api/models exposes gpt-image-2 with its merged capabilities.
    resp = await seeded_app.get("/api/models", headers=auth_header(token))
    assert resp.status_code == 200, resp.text
    models = resp.json()["models"]
    available = {m["model_id"]: m for m in models if m.get("available")}
    assert "gpt-image-2" in available

    # 4. Precheck: clean user, no captcha required.
    pc = await seeded_app.post(
        "/api/jobs/precheck",
        headers=auth_header(token),
        json={"model": "gpt-image-2"},
    )
    assert pc.status_code == 200
    assert pc.json()["captcha_required"] is False

    # 5. Submit one job.
    resp = await submit_job(seeded_app, token, payload=job_payload())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "QUEUED"
    hash_id = body["hash_id"]

    # 6. Wait for the scheduler to dispatch and the stub to land SUCCEEDED.
    await run_scheduler_until_idle(seeded_app, timeout=10.0)
    final = await wait_for_status(
        seeded_app, token, hash_id, expected={"SUCCEEDED"}, timeout=10.0
    )
    assert final == "SUCCEEDED"

    # 7. Archive detail returns the user-visible payload (no admin fields).
    detail = await seeded_app.get(
        f"/api/jobs/{hash_id}", headers=auth_header(token)
    )
    assert detail.status_code == 200, detail.text
    j = detail.json()
    assert j["status"] == "SUCCEEDED"
    assert len(j["images"]) == 1
    # Admin-only fields must NOT be on the user view (design doc §8.6).
    for forbidden in ("provider_used", "cost_cny", "retries"):
        assert forbidden not in j

    # 8. Original-image stream returns content-disposition: attachment.
    img = await seeded_app.get(
        f"/api/jobs/{hash_id}/images/1/original", headers=auth_header(token)
    )
    assert img.status_code == 200
    assert "attachment" in (img.headers.get("content-disposition") or "")
    assert int(img.headers.get("content-length", "0")) > 0
