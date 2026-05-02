"""E2E coverage for the soft-quota penalty (§7.3).

The flow is:

* user crosses the soft-quota threshold
* /api/jobs/precheck returns ``captcha_required=true``
* user submits with a Turnstile token; the token verifies (since the
  test's TURNSTILE_SECRET is empty, any non-empty token is accepted)
* the job lands in the queue with ``SOFT_QUOTA_EXCEEDED`` flag set
* the executor's soft-penalty path runs: optional delay, optional
  probabilistic fail with ``SOFT_QUOTA_PENALTY``

We pin the SoftPenalty knobs via the config center so each test
asserts a deterministic outcome.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.domain.config_center import get_config_center

from .conftest import (
    auth_header,
    job_payload,
    login_user,
    run_scheduler_until_idle,
    seed_provider,
    submit_job,
    wait_for_status,
)


async def _submit_with_captcha(
    client: httpx.AsyncClient, token: str, captcha: str = "fake-cf-token"
) -> httpx.Response:
    payload = json.loads(job_payload())
    payload["captcha_token"] = captcha
    files = [
        ("payload", (None, json.dumps(payload), "application/json")),
    ]
    return await client.post(
        "/api/jobs", headers=auth_header(token), files=files
    )


@pytest.mark.asyncio
async def test_soft_quota_user_without_captcha_token_412(
    seeded_app: httpx.AsyncClient, stub_adapter
) -> None:
    """API gate rejects a soft-quota submission missing ``captcha_token``."""
    await seed_provider(seeded_app, provider_id="oai_gate", label="Gate")
    token = await login_user(
        seeded_app,
        username="gate_user",
        tier="free",
        today_count=8,  # crosses soft (=8)
    )

    resp = await submit_job(seeded_app, token)
    assert resp.status_code == 412, resp.text
    assert resp.json()["detail"]["code"] == "CAPTCHA_REQUIRED"
    # Stub adapter never invoked.
    assert "oai_gate" not in stub_adapter.calls


@pytest.mark.asyncio
async def test_soft_quota_with_captcha_lands_succeeded(
    seeded_app: httpx.AsyncClient, stub_adapter
) -> None:
    """Captcha verified + delay=0 + fail_p=0 ⇒ SUCCEEDED through executor."""
    cc = get_config_center()
    await cc.set_many(
        {
            "soft_penalty.require_turnstile": True,
            "soft_penalty.base_delay_seconds": 0,
            "soft_penalty.max_delay_seconds": 0,
            "soft_penalty.base_fail_probability": 0,
            "soft_penalty.max_fail_probability": 0,
        }
    )
    await seed_provider(seeded_app, provider_id="oai_ok", label="OK")
    token = await login_user(
        seeded_app, username="captcha_ok_user", tier="free", today_count=8
    )

    resp = await _submit_with_captcha(seeded_app, token)
    assert resp.status_code == 200, resp.text
    hash_id = resp.json()["hash_id"]

    await run_scheduler_until_idle(seeded_app, timeout=10.0)
    final = await wait_for_status(
        seeded_app, token, hash_id, expected={"SUCCEEDED"}, timeout=10.0
    )
    assert final == "SUCCEEDED"
    assert stub_adapter.calls.get("oai_ok") == 1


@pytest.mark.asyncio
async def test_soft_quota_probability_fail_lands_soft_quota_penalty(
    seeded_app: httpx.AsyncClient, stub_adapter
) -> None:
    """fail_probability=1 ⇒ deterministic SOFT_QUOTA_PENALTY, no upstream call."""
    cc = get_config_center()
    await cc.set_many(
        {
            "soft_penalty.require_turnstile": True,
            "soft_penalty.base_delay_seconds": 0,
            "soft_penalty.max_delay_seconds": 0,
            "soft_penalty.base_fail_probability": 1.0,
            "soft_penalty.max_fail_probability": 1.0,
        }
    )
    await seed_provider(seeded_app, provider_id="oai_p", label="P")
    token = await login_user(
        seeded_app, username="prob_fail_user", tier="free", today_count=8
    )

    resp = await _submit_with_captcha(seeded_app, token)
    assert resp.status_code == 200
    hash_id = resp.json()["hash_id"]

    await run_scheduler_until_idle(seeded_app, timeout=10.0)
    final = await wait_for_status(
        seeded_app, token, hash_id, expected={"FAILED"}, timeout=10.0
    )
    assert final == "FAILED"

    detail = await seeded_app.get(
        f"/api/jobs/{hash_id}", headers=auth_header(token)
    )
    assert detail.json().get("error") == "SOFT_QUOTA_PENALTY"
    assert "oai_p" not in stub_adapter.calls


@pytest.mark.asyncio
async def test_clean_user_skips_soft_penalty_path(
    seeded_app: httpx.AsyncClient, stub_adapter
) -> None:
    """A user under soft quota is never delayed and never fails by chance."""
    cc = get_config_center()
    await cc.set_many(
        {
            "soft_penalty.require_turnstile": True,
            "soft_penalty.base_delay_seconds": 5,
            "soft_penalty.max_delay_seconds": 5,
            "soft_penalty.base_fail_probability": 1.0,
            "soft_penalty.max_fail_probability": 1.0,
        }
    )
    await seed_provider(seeded_app, provider_id="oai_clean", label="Clean")
    token = await login_user(
        seeded_app, username="clean_user", tier="premium", today_count=0
    )

    resp = await submit_job(seeded_app, token)
    assert resp.status_code == 200
    hash_id = resp.json()["hash_id"]

    await run_scheduler_until_idle(seeded_app, timeout=10.0)
    final = await wait_for_status(
        seeded_app, token, hash_id, expected={"SUCCEEDED"}, timeout=10.0
    )
    assert final == "SUCCEEDED"
    assert stub_adapter.calls.get("oai_clean") == 1
