from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from PIL import Image as PILImage
from sqlalchemy import select

from app.adapters.base import AdapterRegistry, BaseAdapter
from app.db.engine import get_session
from app.db.jobs_repository import JobsRepository
from app.db.models import (
    BillingLedger,
    Image,
    Job,
    Provider,
    ProviderModel,
    ProviderTierAccess,
    User,
)
from app.domain.circuit_breaker import CircuitBreaker
from app.domain.job_executor import JobExecutor
from app.domain.job_queue import QueuedJob
from app.domain.metrics_engine import MetricsEngine
from app.domain.provider_selector import ProviderSelector
from app.domain.quota_guard import get_quota_guard
from app.schemas.normalized import (
    NormalizedImage,
    NormalizedRequest,
    NormalizedResponse,
    ProviderConfig,
    StandardError,
    StandardErrorKind,
)
from app.schemas.provider import CapabilityField
from app.utils.crypto import encrypt
from app.utils.ids import new_user_id
from app.utils.security import hash_password


@dataclass
class RouteByProviderAdapter(BaseAdapter):
    adapter_type = "fake_route"
    display_name = "Fake Route"
    description = "Test adapter"

    outcomes: dict[str, Any] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    async def generate(
        self,
        provider: ProviderConfig,
        request: NormalizedRequest,
    ) -> NormalizedResponse:
        self.calls.append(provider.id)
        outcome = self.outcomes[provider.id]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def supported_models(self) -> list[str]:
        return ["gpt-image-2"]

    def capability_schema(self) -> list[CapabilityField]:
        return []


async def _bootstrap() -> None:
    from app.db import seed
    from app.domain.config_center import get_config_center
    from app.domain.tier_config import get_tier_config

    await seed.bootstrap()
    await get_config_center().load_from_db()
    await get_tier_config().load_from_db()


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    img = PILImage.new("RGB", (32, 24), color=(250, 210, 40))
    img.save(buf, format="PNG")
    return buf.getvalue()


def _response() -> NormalizedResponse:
    return NormalizedResponse(
        images=[NormalizedImage(data=_png_bytes(), mime="image/png")],
        image_count=1,
        raw={"ok": True, "b64_json": "<redacted>"},
    )


async def _create_user(tier: str = "premium", *, today_count: int = 0) -> User:
    user = User(
        id=new_user_id(),
        username=f"user_{tier}_{today_count}",
        password_hash=hash_password("pw123456"),
        role="user",
        tier=tier,
        today_count=today_count,
    )
    async with get_session() as session:
        session.add(user)
    return user


async def _seed_provider(
    provider_id: str,
    *,
    cost: float,
    balance: float = 10.0,
) -> None:
    async with get_session() as session:
        session.add(
            Provider(
                id=provider_id,
                label=provider_id.upper(),
                adapter_type="fake_route",
                base_url="https://example.test",
                api_key_enc=encrypt(f"sk-{provider_id}"),
                cost_per_image_cny=cost,
                initial_balance_cny=balance,
                balance_cny=balance,
                max_concurrency=10,
                rpm_limit=600,
                circuit_state="healthy",
            )
        )
        session.add(
            ProviderModel(
                provider_id=provider_id,
                model_id="gpt-image-2",
                capabilities_json=json.dumps({"n_max": 4}),
                enabled=1,
            )
        )
        for tier in ("vip", "premium", "standard", "free"):
            session.add(ProviderTierAccess(provider_id=provider_id, tier=tier))


async def _create_job(
    user: User,
    *,
    flags: dict[str, Any] | None = None,
) -> QueuedJob:
    params = {"model": "gpt-image-2", "prompt": "draw a banana", "n": 1}
    repo = JobsRepository()
    async with get_session() as session:
        created = await repo.insert_queued(
            user_id=user.id,
            tier_at_submit=user.tier,
            model="gpt-image-2",
            params_json=json.dumps(params),
            flags_json=json.dumps(flags or {}),
            session=session,
        )
    async with get_session() as session:
        job = (
            await session.execute(select(Job).where(Job.hash_id == created.hash_id))
        ).scalar_one()
    return QueuedJob.from_job(job)


def _executor(adapter: RouteByProviderAdapter) -> tuple[JobExecutor, MetricsEngine]:
    registry = AdapterRegistry()
    registry.register(adapter)
    metrics = MetricsEngine(window_seconds=300)
    breaker = CircuitBreaker()
    selector = ProviderSelector(metrics=metrics, breaker=breaker)
    return (
        JobExecutor(
            selector=selector,
            metrics=metrics,
            breaker=breaker,
            registry=registry,
            sleep=_no_sleep,
        ),
        metrics,
    )


async def _no_sleep(seconds: float) -> None:
    return None


async def _job_row(hash_id: str) -> Job:
    async with get_session() as session:
        return (
            await session.execute(select(Job).where(Job.hash_id == hash_id))
        ).scalar_one()


@pytest.mark.asyncio
async def test_executor_success_stores_image_and_deducts_ledger(
    initialized_db: None,
) -> None:
    await _bootstrap()
    await _seed_provider("p1", cost=0.25)
    user = await _create_user()
    await get_quota_guard().record_usage(user.id)
    queued = await _create_job(user)

    adapter = RouteByProviderAdapter(outcomes={"p1": _response()})
    executor, metrics = _executor(adapter)
    await executor.execute(queued)

    job = await _job_row(queued.hash_id)
    assert job.status == "SUCCEEDED"
    assert job.provider_used == "p1"
    assert job.cost_cny == pytest.approx(0.25)

    async with get_session() as session:
        images = (
            await session.execute(select(Image).where(Image.job_id == queued.job_id))
        ).scalars().all()
        ledger = (
            await session.execute(
                select(BillingLedger).where(BillingLedger.provider_id == "p1")
            )
        ).scalar_one()
        provider = (
            await session.execute(select(Provider).where(Provider.id == "p1"))
        ).scalar_one()

    assert len(images) == 1
    assert images[0].width == 32
    assert images[0].height == 24
    assert ledger.cost_cny == pytest.approx(0.25)
    assert provider.balance_cny == pytest.approx(9.75)
    assert metrics.success_rate("p1", "gpt-image-2") == 1.0


@pytest.mark.asyncio
async def test_executor_falls_back_to_second_provider(initialized_db: None) -> None:
    await _bootstrap()
    await _seed_provider("cheap_fail", cost=0.10)
    await _seed_provider("ok", cost=0.20)
    user = await _create_user()
    await get_quota_guard().record_usage(user.id)
    queued = await _create_job(user)

    adapter = RouteByProviderAdapter(
        outcomes={
            "cheap_fail": StandardError(
                StandardErrorKind.UPSTREAM_TIMEOUT,
                "timeout",
            ),
            "ok": _response(),
        }
    )
    executor, metrics = _executor(adapter)
    await executor.execute(queued)

    job = await _job_row(queued.hash_id)
    assert job.status == "SUCCEEDED"
    assert job.provider_used == "ok"
    assert job.retries == 1
    assert adapter.calls[:2] == ["cheap_fail", "ok"]
    assert metrics.success_rate("cheap_fail", "gpt-image-2") == 0.0


@pytest.mark.asyncio
async def test_no_provider_available_fails_and_refunds_quota(
    initialized_db: None,
) -> None:
    await _bootstrap()
    user = await _create_user(today_count=0)
    await get_quota_guard().record_usage(user.id)
    queued = await _create_job(user)

    adapter = RouteByProviderAdapter(outcomes={})
    executor, _ = _executor(adapter)
    await executor.execute(queued)

    job = await _job_row(queued.hash_id)
    async with get_session() as session:
        today = (
            await session.execute(select(User.today_count).where(User.id == user.id))
        ).scalar_one()
    assert job.status == "FAILED"
    assert job.status_reason == "NO_PROVIDER_AVAILABLE"
    assert today == 0


@pytest.mark.asyncio
async def test_soft_quota_without_captcha_fails_before_upstream(
    initialized_db: None,
) -> None:
    await _bootstrap()
    await _seed_provider("p1", cost=0.25)
    user = await _create_user(tier="free", today_count=8)
    await get_quota_guard().record_usage(user.id)
    queued = await _create_job(user, flags={"SOFT_QUOTA_EXCEEDED": True})

    adapter = RouteByProviderAdapter(outcomes={"p1": _response()})
    executor, metrics = _executor(adapter)
    await executor.execute(queued)

    job = await _job_row(queued.hash_id)
    assert job.status == "FAILED"
    assert job.status_reason == "CAPTCHA_REQUIRED"
    assert adapter.calls == []
    assert metrics.recent_calls_in_60s("p1") == 0
