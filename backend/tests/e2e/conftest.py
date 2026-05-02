"""Shared E2E fixtures and helpers.

The e2e suite drives the FastAPI app through ``httpx.AsyncClient`` like
the unit tests, but each test composes a full user-journey: login →
provider setup → submit → SSE → completion. We register a deterministic
stub adapter so we never depend on the real upstream during the suite.

These tests intentionally skip the bltcy-style real keys; PR-18 spec
calls for compose-style mock testing rather than the live keys we got
from the user. The latter are reserved for the admin "Test" button.
"""

from __future__ import annotations

import asyncio
import io
import json
from datetime import datetime, timezone
from typing import Any, Iterable

import httpx
import pytest
from PIL import Image as PILImage

from app.adapters.base import AdapterRegistry, BaseAdapter
from app.schemas.normalized import (
    NormalizedImage,
    NormalizedRequest,
    NormalizedResponse,
    ProviderConfig,
    StandardError,
    StandardErrorKind,
)


# ---------------------------------------------------------------------------
# Stub adapter — deterministic, no network
# ---------------------------------------------------------------------------


class StubAdapter(BaseAdapter):
    """Adapter that returns a fixed PNG. Used by every e2e test.

    Behaviour is overridden per-provider via ``provider_outcomes``:
    set the entry to ``"ok"`` for success, ``"fail"`` for a fake
    upstream error. Defaults to ``"ok"``. Tests use this to engineer
    fallback / circuit-breaker scenarios without standing up a real
    HTTP server.

    Counters are exposed so a test can assert "this provider was tried
    N times" after the executor finished.
    """

    adapter_type = "stub_e2e"
    display_name = "E2E Stub"
    description = "Deterministic stub adapter used only in the e2e suite."

    def __init__(self) -> None:
        self.provider_outcomes: dict[str, str] = {}
        self.calls: dict[str, int] = {}

    def supported_models(self) -> list[str]:
        return [
            "gpt-image-2",
            "gemini-3-pro-image-preview",
            "gemini-3.1-flash-image-preview",
        ]

    def configure(self, provider_id: str, outcome: str) -> None:
        self.provider_outcomes[provider_id] = outcome

    def reset(self) -> None:
        self.provider_outcomes.clear()
        self.calls.clear()

    async def generate(
        self,
        provider: ProviderConfig,
        request: NormalizedRequest,
    ) -> NormalizedResponse:
        self.calls[provider.id] = self.calls.get(provider.id, 0) + 1
        outcome = self.provider_outcomes.get(provider.id, "ok")
        if outcome == "fail":
            raise StandardError(
                StandardErrorKind.UPSTREAM_ERROR,
                f"stub adapter forced failure for {provider.id}",
            )
        if outcome == "timeout":
            raise StandardError(
                StandardErrorKind.UPSTREAM_TIMEOUT,
                f"stub adapter forced timeout for {provider.id}",
            )

        png = _png_bytes()
        image = NormalizedImage(data=png, mime="image/png")
        return NormalizedResponse(
            images=[image],
            image_count=1,
            raw={"stub": True, "provider": provider.id},
        )


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    PILImage.new("RGB", (10, 10), color=(120, 200, 255)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def stub_adapter() -> Iterable[StubAdapter]:
    """Register the stub adapter into the global registry for the test.

    The registry is a singleton, but :func:`reset_for_tests` exists for
    exactly this purpose. Each e2e test starts with a fresh adapter.
    """
    registry = AdapterRegistry.instance()
    # Don't blow away the production-discovered adapters: they're
    # needed for routes that look up adapter_type from the DB. We just
    # register our stub alongside them. ``reset_for_tests`` removes
    # the stub after the test finishes.
    adapter = StubAdapter()
    registry.register(adapter)
    try:
        yield adapter
    finally:
        registry.reset_for_tests()
        # Re-discover so other tests in the suite still see openai_v1
        # / gemini_v1beta. Discovery is idempotent.
        registry.discover()


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


async def login_admin(client: httpx.AsyncClient) -> str:
    resp = await client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-admin-password"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def create_user(
    *,
    username: str,
    tier: str = "premium",
    today_count: int = 0,
    override_soft_quota: int | None = None,
    override_hard_quota: int | None = None,
) -> str:
    """Insert a user row directly. Returns the new user_id."""
    from app.db.engine import get_session
    from app.db.models import User
    from app.utils.ids import new_user_id
    from app.utils.security import hash_password

    user_id = new_user_id()
    today = datetime.now(timezone.utc).date().isoformat()
    async with get_session() as session:
        session.add(
            User(
                id=user_id,
                username=username,
                password_hash=hash_password("e2epassword"),
                role="user",
                tier=tier,
                today_count=today_count,
                today_reset_date=today,
                override_soft_quota=override_soft_quota,
                override_hard_quota=override_hard_quota,
            )
        )
    return user_id


async def login_user(
    client: httpx.AsyncClient,
    *,
    username: str,
    tier: str = "premium",
    today_count: int = 0,
    override_soft_quota: int | None = None,
    override_hard_quota: int | None = None,
) -> str:
    """Create + login a user, return their access token."""
    await create_user(
        username=username,
        tier=tier,
        today_count=today_count,
        override_soft_quota=override_soft_quota,
        override_hard_quota=override_hard_quota,
    )
    resp = await client.post(
        "/api/auth/login",
        json={"username": username, "password": "e2epassword"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Provider helpers
# ---------------------------------------------------------------------------


async def seed_provider(
    client: httpx.AsyncClient,
    *,
    provider_id: str,
    label: str,
    cost: float = 0.10,
    balance: float = 5.0,
    adapter_type: str = "stub_e2e",
    tier_access: list[str] | None = None,
    model_id: str = "gpt-image-2",
    capabilities: dict[str, Any] | None = None,
) -> None:
    admin_token = await login_admin(client)
    payload: dict[str, Any] = {
        "provider_id": provider_id,
        "label": label,
        "adapter_type": adapter_type,
        "base_url": f"https://example.test/{provider_id}",
        "api_key": f"sk-fake-{provider_id}-FQBR",
        "cost_per_image_cny": cost,
        "initial_balance_cny": balance,
        "supported_models": [
            {
                "model_id": model_id,
                "capabilities": capabilities
                or _default_capabilities_for(model_id),
            }
        ],
        "tier_access": tier_access or ["vip", "premium", "standard", "free"],
    }
    resp = await client.post(
        "/api/admin/providers",
        headers=auth_header(admin_token),
        json=payload,
    )
    assert resp.status_code == 201, resp.text


def _default_capabilities_for(model_id: str) -> dict[str, Any]:
    if model_id == "gpt-image-2":
        return {
            "n_max": 10,
            "size": ["1024x1024", "1536x1024", "1024x1536", "auto"],
            "quality": ["low", "medium", "high", "auto"],
            "output_format": ["png", "jpeg", "webp"],
            "background": ["auto", "opaque"],
            "moderation": ["auto", "low"],
            "max_reference_images": 16,
            "max_prompt_chars": 32000,
            "supports_mask": True,
            "stream": True,
            "partial_images_max": 3,
        }
    return {
        "n_max": 1,
        "aspect_ratio": [
            "1:1",
            "2:3",
            "3:2",
            "3:4",
            "4:3",
            "16:9",
            "9:16",
            "21:9",
        ],
        "image_size": ["1K", "2K", "4K"],
        "max_reference_images": 14,
    }


def job_payload(**overrides: Any) -> str:
    base: dict[str, Any] = {
        "model": "gpt-image-2",
        "prompt": "a still-life of three overripe bananas on a marble slab",
        "n": 1,
        "size": "1024x1024",
        "output_format": "png",
        "background": "auto",
    }
    base.update(overrides)
    return json.dumps(base)


async def submit_job(
    client: httpx.AsyncClient,
    token: str,
    *,
    payload: str | None = None,
    references: list[tuple[str, bytes, str]] | None = None,
) -> httpx.Response:
    files: list[tuple[str, tuple[str | None, Any, str]]] = [
        ("payload", (None, payload or job_payload(), "application/json")),
    ]
    for name, blob, mime in references or []:
        files.append((name, (f"{name}.png", blob, mime)))
    return await client.post(
        "/api/jobs", headers=auth_header(token), files=files
    )


async def wait_for_status(
    client: httpx.AsyncClient,
    token: str,
    hash_id: str,
    *,
    expected: set[str],
    timeout: float = 8.0,
) -> str:
    """Poll the archive endpoint until status enters ``expected``."""
    deadline = asyncio.get_event_loop().time() + timeout
    last_status = "?"
    while asyncio.get_event_loop().time() < deadline:
        resp = await client.get(
            f"/api/jobs/{hash_id}", headers=auth_header(token)
        )
        if resp.status_code == 200:
            last_status = resp.json()["status"]
            if last_status in expected:
                return last_status
        await asyncio.sleep(0.05)
    raise AssertionError(
        f"job {hash_id} never reached {expected}; last seen={last_status}"
    )


async def run_scheduler_until_idle(
    client: httpx.AsyncClient,
    *,
    timeout: float = 6.0,
) -> None:
    """Drive the in-process scheduler one tick at a time.

    The seeded_app fixture starts a background scheduler task. Because
    httpx + pytest run on a single event loop, we yield control with
    ``asyncio.sleep(0)`` long enough for the scheduler to drain the
    queue. We bail when the queue is empty AND the scheduler has zero
    in-flight workers.
    """
    from app.domain.job_queue import get_job_queue
    from app.domain.job_scheduler import get_job_scheduler

    queue = get_job_queue()
    scheduler = get_job_scheduler()
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        size = await queue.size()
        if size == 0 and scheduler.running_count == 0:
            return
        await asyncio.sleep(0.05)


__all__ = [
    "StubAdapter",
    "auth_header",
    "create_user",
    "job_payload",
    "login_admin",
    "login_user",
    "run_scheduler_until_idle",
    "seed_provider",
    "stub_adapter",
    "submit_job",
    "wait_for_status",
]
