"""Worker execution for queued generation jobs.

The executor is the bridge between the scheduler and the provider stack:
it moves a job to RUNNING, applies any soft-quota penalty, selects
providers, tries the fallback chain, stores images, deducts balance, records
metrics, and lands the terminal lifecycle state.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

from PIL import Image as PILImage
from pydantic import ValidationError
from sqlalchemy import select, update

from app.adapters.base import AdapterRegistry, BaseAdapter
from app.config import get_settings
from app.db.engine import get_session
from app.db.models import Image, Job, User
from app.domain.circuit_breaker import CircuitBreaker, get_circuit_breaker
from app.domain.job_lifecycle import (
    FAILED,
    RUNNING,
    SUCCEEDED,
    InvalidTransition,
    JobLifecycle,
    get_job_lifecycle,
)
from app.domain.job_queue import QueuedJob
from app.domain.metrics_engine import MetricsEngine, get_metrics_engine
from app.domain.provider_ledger import ProviderLedger, get_provider_ledger
from app.domain.provider_selector import (
    CandidateProvider,
    ProviderSelector,
    ScoredCandidate,
    get_provider_selector,
)
from app.domain.quota_guard import QuotaGuard, get_quota_guard
from app.domain.runtime_configs import ProviderScoringConfig
from app.domain.soft_penalty import SoftPenalty, get_soft_penalty, is_soft_quota_job
from app.schemas.normalized import (
    NormalizedImage,
    NormalizedReference,
    NormalizedRequest,
    NormalizedResponse,
    ProviderConfig,
    StandardError,
    StandardErrorKind,
)
from app.services import image_io
from app.utils.crypto import decrypt
from app.utils.ids import new_image_id
from app.utils.redact import redact_payload

logger = logging.getLogger("txt2img.executor")

SleepFunc = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class JobExecutionContext:
    """Loaded DB snapshot needed to execute a job."""

    job_id: str
    hash_id: str
    user: User
    model: str
    request: NormalizedRequest
    flags: dict[str, Any]


class JobExecutor:
    """Execute one queued job to a terminal state."""

    def __init__(
        self,
        *,
        selector: ProviderSelector | None = None,
        ledger: ProviderLedger | None = None,
        lifecycle: JobLifecycle | None = None,
        metrics: MetricsEngine | None = None,
        breaker: CircuitBreaker | None = None,
        quota_guard: QuotaGuard | None = None,
        soft_penalty: SoftPenalty | None = None,
        registry: AdapterRegistry | None = None,
        scoring_config: ProviderScoringConfig | None = None,
        sleep: SleepFunc = asyncio.sleep,
    ) -> None:
        self._selector = selector or get_provider_selector()
        self._ledger = ledger or get_provider_ledger()
        self._lifecycle = lifecycle or get_job_lifecycle()
        self._metrics = metrics or get_metrics_engine()
        self._breaker = breaker or get_circuit_breaker()
        self._quota_guard = quota_guard or get_quota_guard()
        self._soft_penalty = soft_penalty or get_soft_penalty()
        self._registry = registry or AdapterRegistry.instance()
        self._scoring = scoring_config or ProviderScoringConfig()
        self._sleep = sleep

    async def execute(self, queued: QueuedJob) -> None:
        """Run ``queued`` until SUCCEEDED/FAILED or it is no longer runnable."""
        # Stamp the worker's contextvars so every downstream logger
        # call inherits the job id / user id without us threading them
        # through every helper.
        from app.utils import log_context

        log_context.set_job_id(queued.hash_id)
        log_context.set_user_id(queued.user_id)
        logger.info(
            "executor: start hash_id=%s user_id=%s tier=%s model=%s",
            queued.hash_id,
            queued.user_id,
            queued.tier,
            queued.model,
        )
        try:
            ctx = await self._load_context(queued.hash_id)
        except Exception:
            logger.exception("executor: failed to load job=%s", queued.hash_id)
            return

        if ctx is None:
            return

        try:
            await self._mark_running(ctx)
            logger.info(
                "executor: marked RUNNING hash_id=%s user=%s model=%s",
                ctx.hash_id,
                ctx.user.id,
                ctx.model,
            )
        except InvalidTransition:
            # The job was likely cancelled/deleted after being enqueued.
            logger.info("executor: job=%s no longer QUEUED; skip", queued.hash_id)
            return

        if is_soft_quota_job(ctx.flags):
            logger.info(
                "executor: soft-quota path hash_id=%s flags=%s",
                ctx.hash_id,
                sorted(ctx.flags.keys()),
            )
            should_continue = await self._apply_soft_penalty(ctx)
            if not should_continue:
                return

        candidates = await self._selector.select(
            ctx.user,
            ctx.request,
            top_k=self._max_retries_per_job(),
            hash_id=ctx.hash_id,
        )
        if not candidates:
            logger.warning(
                "executor: no provider available hash_id=%s user=%s model=%s",
                ctx.hash_id,
                ctx.user.id,
                ctx.model,
            )
            await self._fail(ctx, "NO_PROVIDER_AVAILABLE", refund_quota=True)
            return

        logger.info(
            "executor: candidates selected hash_id=%s n=%d providers=%s",
            ctx.hash_id,
            len(candidates),
            [c.provider.provider_id for c in candidates],
        )
        await self._mark_started(ctx.hash_id)
        await self._try_candidates(ctx, candidates)

    async def _load_context(self, hash_id: str) -> JobExecutionContext | None:
        async with get_session() as session:
            row = (
                await session.execute(
                    select(Job, User)
                    .join(User, Job.user_id == User.id)
                    .where(Job.hash_id == hash_id)
                )
            ).one_or_none()
            if row is None:
                logger.warning("executor: job=%s disappeared before run", hash_id)
                return None
            job, user = row
            flags = _safe_json_dict(job.flags_json)
            params = _safe_json_dict(job.params_json)
            params.setdefault("model", job.model)
            params.setdefault("prompt", "")

            # Re-attach reference uploads from disk so the adapter sees
            # the full ``NormalizedRequest``. ``params_json`` does NOT
            # carry references — they live in the ``job_references`` join
            # table + ``data/jobs/<hash>/refs/`` on disk to keep the
            # ``params_json`` row from blowing past the DB column limit.
            params["references"] = await self._load_reference_payload(
                session, job_id=job.id
            )
            try:
                request = NormalizedRequest.model_validate(params)
            except ValidationError as exc:
                logger.warning("executor: invalid params for job=%s: %s", hash_id, exc)
                # Invalid persisted params are a programming error from a
                # future create-job path. Fail the job if it is still QUEUED.
                await self._lifecycle.transition(
                    hash_id,
                    FAILED,
                    reason="INVALID_PARAMETER",
                )
                return None
            return JobExecutionContext(
                job_id=job.id,
                hash_id=job.hash_id,
                user=user,
                model=job.model,
                request=request,
                flags=flags,
            )

    async def _load_reference_payload(
        self, session, *, job_id: str
    ) -> list[dict[str, Any]]:
        """Read every ``job_references`` row for a job and produce a list
        of dicts that ``NormalizedReference`` can validate.

        Reference bytes live on disk under ``data/jobs/<hash>/refs/``;
        we read each file, base64-encode it, and emit ``{order, mime,
        data_b64, filename}`` so the adapter sees the same payload it
        would have if the route had inlined them into ``params_json``.
        Without this step ``request.references`` is silently empty and
        the adapter sends an /v1/images/edits call without an ``image``
        field, which 4xx's at upstream — see Bug audit in PR #92.
        """
        from app.db.models import JobReference  # avoid circular import
        from app.services import image_io

        rows = (
            await session.execute(
                select(JobReference)
                .where(JobReference.job_id == job_id)
                .order_by(JobReference.ref_order)
            )
        ).scalars().all()
        out: list[dict[str, Any]] = []
        data_root = image_io._data_root()
        for ref in rows:
            try:
                rel = ref.rel_path
                if rel.startswith("/"):
                    rel = rel[1:]
                blob = (data_root / rel).read_bytes()
            except (OSError, FileNotFoundError) as exc:
                logger.warning(
                    "executor: missing reference file for job_id=%s order=%d (%s)",
                    job_id, ref.ref_order, exc,
                )
                continue
            out.append({
                "order": ref.ref_order,
                "mime": ref.mime,
                "data_b64": base64.b64encode(blob).decode("ascii"),
                "filename": ref.filename,
            })
        return out

    async def _mark_running(self, ctx: JobExecutionContext) -> None:
        now = datetime.now(timezone.utc)
        user_state = await self._snapshot_user_state(ctx)
        async with get_session() as session:
            await session.execute(
                update(Job)
                .where(Job.hash_id == ctx.hash_id)
                .values(dispatched_at=now, updated_at=now)
            )
            result = await self._lifecycle.transition(
                ctx.hash_id,
                RUNNING,
                session=session,
                extra_timeline={
                    "worker_id": "local",
                    "user_state": user_state,
                },
            )
        await self._lifecycle.publish_transition(result)

    async def _snapshot_user_state(
        self, ctx: JobExecutionContext
    ) -> dict[str, Any]:
        """Capture the user's quota / penalty state at submit time.

        Persisted in ``timeline.jsonl`` so the admin inspector can
        reconstruct what the user looked like when scheduling decided
        to run them. Best-effort — every field has a default so a
        missing tier config never breaks job execution.
        """
        try:
            today_count = await self._quota_guard.today_count(ctx.user.id)
        except Exception:  # pragma: no cover
            today_count = int(getattr(ctx.user, "today_count", 0) or 0)

        try:
            from app.domain.tier_config import get_tier_config

            soft, hard = get_tier_config().effective_quotas(ctx.user)
        except Exception:  # pragma: no cover
            soft, hard = 0, 0

        try:
            recent_n = await self._recent_failures(ctx.user.id, ctx.hash_id)
        except Exception:  # pragma: no cover
            recent_n = 0

        return {
            "tier": ctx.user.tier,
            "today_count": int(today_count or 0),
            "soft_quota_effective": int(soft or 0),
            "hard_quota_effective": int(hard or 0),
            "soft_quota_triggered": bool(
                ctx.flags.get("SOFT_QUOTA_EXCEEDED") or False
            ),
            "captcha_required": bool(ctx.flags.get("captcha_required") or False),
            "captcha_verified": bool(ctx.flags.get("captcha_verified") or False),
            "recent_fail_rate_n": int(recent_n),
            "recent_fail_rate_total": 10,
        }

    async def _recent_failures(self, user_id: str, exclude_hash_id: str) -> int:
        async with get_session() as session:
            rows = list(
                (
                    await session.execute(
                        select(Job.status)
                        .where(
                            Job.user_id == user_id,
                            Job.hash_id != exclude_hash_id,
                            Job.status.in_(("SUCCEEDED", "FAILED", "CANCELLED")),
                        )
                        .order_by(Job.finished_at.desc(), Job.created_at.desc())
                        .limit(10)
                    )
                ).all()
            )
        return sum(1 for (s,) in rows if s == "FAILED")

    async def _apply_soft_penalty(self, ctx: JobExecutionContext) -> bool:
        today_count = await self._quota_guard.today_count(ctx.user.id)
        plan = self._soft_penalty.plan(
            ctx.user,
            ctx.flags,
            today_count=today_count,
        )

        if plan.require_turnstile and not plan.captcha_verified:
            await self._fail(ctx, "CAPTCHA_REQUIRED", refund_quota=False)
            return False

        if plan.delay_seconds > 0:
            await self._sleep(plan.delay_seconds)

        if self._soft_penalty.should_fail(plan.fail_probability):
            await self._fail(ctx, "SOFT_QUOTA_PENALTY", refund_quota=False)
            return False
        return True

    async def _try_candidates(
        self,
        ctx: JobExecutionContext,
        candidates: list[ScoredCandidate],
    ) -> None:
        max_attempts = self._max_retries_per_job()
        attempts = candidates[:max_attempts]
        last_error: StandardError | None = None

        for attempt_no, scored in enumerate(attempts, start=1):
            provider = scored.provider
            try:
                adapter = self._registry.get(provider.adapter_type)
            except KeyError:
                last_error = StandardError(
                    StandardErrorKind.UNSUPPORTED_MODEL,
                    f"Unknown adapter_type {provider.adapter_type!r}.",
                )
                await self._record_attempt_failure(
                    ctx,
                    provider,
                    attempt_no,
                    last_error,
                )
                continue
            try:
                response = await self._call_adapter(
                    adapter,
                    provider,
                    ctx.request,
                    ctx.hash_id,
                    attempt_no,
                )
            except StandardError as exc:
                last_error = exc
                await self._record_attempt_failure(
                    ctx,
                    provider,
                    attempt_no,
                    exc,
                )
                continue

            try:
                image_payloads = await self._store_images(
                    ctx,
                    response.images,
                    provider_id=provider.provider_id,
                )
                deduction = await self._ledger.deduct(
                    provider.provider_id,
                    ctx.job_id,
                    image_count=len(response.images),
                )
            except Exception:
                # Storage / ledger failures are internal; falling back to another
                # provider would risk duplicate outputs or double-spending.
                logger.exception(
                    "executor: post-upstream finalisation failed job=%s provider=%s",
                    ctx.hash_id,
                    provider.provider_id,
                )
                await self._fail(ctx, "INTERNAL_ERROR", refund_quota=False)
                return

            await self._mark_success(
                ctx,
                provider_id=provider.provider_id,
                retries=attempt_no - 1,
                cost_cny=deduction.cost_cny,
            )
            try:
                await asyncio.to_thread(
                    image_io.update_routing_chosen,
                    ctx.hash_id,
                    provider.provider_id,
                )
            except Exception:  # pragma: no cover
                logger.warning(
                    "executor: failed to record chosen provider for job=%s",
                    ctx.hash_id,
                )
            await self._publish_success_result(
                ctx,
                image_payloads=image_payloads,
                provider_id=provider.provider_id,
            )
            await self._lifecycle.transition(ctx.hash_id, SUCCEEDED)
            logger.info(
                "executor: SUCCEEDED hash_id=%s provider=%s attempt=%d "
                "images=%d cost_cny=%.4f",
                ctx.hash_id,
                provider.provider_id,
                attempt_no,
                len(response.images),
                float(deduction.cost_cny or 0.0),
            )
            return

        reason = "ALL_PROVIDERS_FAILED"
        if (
            last_error is not None
            and last_error.kind == StandardErrorKind.INVALID_PARAMETER
        ):
            reason = "INVALID_PARAMETER"
        logger.warning(
            "executor: FAILED hash_id=%s reason=%s attempts=%d last_error=%s",
            ctx.hash_id,
            reason,
            len(attempts),
            (last_error.kind.value if last_error is not None else None),
        )
        await self._fail(ctx, reason, refund_quota=False)

    async def _call_adapter(
        self,
        adapter: BaseAdapter,
        provider: CandidateProvider,
        request: NormalizedRequest,
        hash_id: str,
        attempt_no: int,
    ) -> NormalizedResponse:
        provider_config = ProviderConfig(
            id=provider.provider_id,
            base_url=provider.base_url,
            api_key=decrypt(provider.api_key_enc),
            adapter_type=provider.adapter_type,
        )

        # Capture provider state at the moment of dispatch — admin
        # inspector reads this back to explain failure / latency. Done
        # before the lease so the concurrency value reflects the queue
        # the call is about to enter, not after it incremented.
        snapshot = self._capture_provider_snapshot(provider, request.model)
        started_at_iso = datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )

        started = time.perf_counter()
        self._metrics.lease_concurrency(provider.provider_id)
        try:
            response = await adapter.generate(provider_config, request)
            latency_ms = (time.perf_counter() - started) * 1000.0
            if not response.images:
                raise StandardError(
                    StandardErrorKind.EMPTY_RESPONSE,
                    "Upstream returned no images.",
                )
            self._metrics.record_call(
                provider.provider_id,
                request.model,
                ok=True,
                latency_ms=latency_ms,
            )
            await self._breaker.observe(provider.provider_id, success=True)
            await asyncio.to_thread(
                image_io.write_upstream_log,
                hash_id,
                attempt_no,
                {
                    "provider_id": provider.provider_id,
                    "ok": True,
                    "started_at": started_at_iso,
                    "latency_ms": round(latency_ms, 3),
                    "response": redact_payload(_json_safe(response.raw)),
                    "image_count": len(response.images),
                    "provider_snapshot": snapshot,
                },
            )
            return response
        except Exception as exc:
            latency_ms = (time.perf_counter() - started) * 1000.0
            normalized = adapter.normalize_error(exc)
            self._metrics.record_call(
                provider.provider_id,
                request.model,
                ok=False,
                latency_ms=latency_ms,
                error_kind=normalized.kind,
            )
            await self._breaker.observe(provider.provider_id, success=False)
            # Redact the *whole* normalized error payload, not just the
            # human-readable ``message``: ``to_dict`` may also expose
            # ``upstream_body_excerpt`` / nested headers that can echo a
            # token verbatim. The walking redactor scrubs every string
            # leaf and every sensitive JSON key in one pass.
            error_payload = redact_payload(normalized.to_dict())
            await asyncio.to_thread(
                image_io.write_upstream_log,
                hash_id,
                attempt_no,
                {
                    "provider_id": provider.provider_id,
                    "ok": False,
                    "started_at": started_at_iso,
                    "latency_ms": round(latency_ms, 3),
                    "error": error_payload,
                    "provider_snapshot": snapshot,
                },
            )
            raise normalized from exc
        finally:
            self._metrics.release_concurrency(provider.provider_id)

    async def _record_attempt_failure(
        self,
        ctx: JobExecutionContext,
        provider: CandidateProvider,
        attempt_no: int,
        exc: StandardError,
    ) -> None:
        await self._set_retries(ctx.hash_id, attempt_no)
        logger.info(
            "executor: job=%s provider=%s attempt=%d failed kind=%s",
            ctx.hash_id,
            provider.provider_id,
            attempt_no,
            exc.kind.value,
        )

    async def _store_images(
        self,
        ctx: JobExecutionContext,
        images: list[NormalizedImage],
        *,
        provider_id: str,
    ) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        rows: list[Image] = []

        for idx, img in enumerate(images, start=1):
            stored = await asyncio.to_thread(
                _store_one_image,
                ctx.hash_id,
                idx,
                img.data,
                img.mime,
            )
            image_id = new_image_id()
            rows.append(
                Image(
                    id=image_id,
                    job_id=ctx.job_id,
                    img_order=idx,
                    original_path=stored["original_path"],
                    thumb_path=stored["thumb_path"],
                    width=stored["width"],
                    height=stored["height"],
                    format=stored["format"],
                    file_size_bytes=stored["file_size_bytes"],
                )
            )
            payloads.append(
                {
                    "image_id": image_id,
                    "order": idx,
                    "thumb_url": f"/api/jobs/{ctx.hash_id}/images/{idx}/thumb",
                    "width": stored["width"],
                    "height": stored["height"],
                    "format": stored["format"],
                }
            )

        async with get_session() as session:
            session.add_all(rows)
            await session.execute(
                update(Job)
                .where(Job.hash_id == ctx.hash_id)
                .values(
                    provider_used=provider_id,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            # PRD §6.7 / §3.2: if this job is bound to a finalized session,
            # the arrival of new images automatically reverts it to
            # ``judging`` — the user must re-finalize once the new images
            # are judged. Fetch the job row to get session_id, then
            # cascade.
            from app.db.models import Session as SessionRow

            job_row = (
                await session.execute(
                    select(Job).where(Job.hash_id == ctx.hash_id)
                )
            ).scalar_one_or_none()
            if job_row is not None and job_row.session_id:
                sess_row = (
                    await session.execute(
                        select(SessionRow).where(
                            SessionRow.id == job_row.session_id
                        )
                    )
                ).scalar_one_or_none()
                if sess_row is not None:
                    if sess_row.picker_state == "finalized":
                        sess_row.picker_state = "judging"
                        sess_row.finalized_at = None
                    elif sess_row.picker_state == "not_started":
                        # New images don't change "not_started" if all
                        # are unjudged (which they are at insert time).
                        pass
                    sess_row.updated_at = datetime.now(timezone.utc)
        return payloads

    async def _mark_success(
        self,
        ctx: JobExecutionContext,
        *,
        provider_id: str,
        retries: int,
        cost_cny: float,
    ) -> None:
        async with get_session() as session:
            await session.execute(
                update(Job)
                .where(Job.hash_id == ctx.hash_id)
                .values(
                    provider_used=provider_id,
                    retries=retries,
                    cost_cny=cost_cny,
                    updated_at=datetime.now(timezone.utc),
                )
            )

    async def _set_retries(self, hash_id: str, attempts: int) -> None:
        async with get_session() as session:
            await session.execute(
                update(Job)
                .where(Job.hash_id == hash_id)
                .values(
                    retries=max(0, attempts),
                    updated_at=datetime.now(timezone.utc),
                )
            )

    def _capture_provider_snapshot(
        self, provider: CandidateProvider, model: str
    ) -> dict[str, Any]:
        """Snapshot the provider's live state for an attempt log.

        Read-only; never raises. Missing data points come back as
        ``None`` so the inspector UI can render a ``—`` placeholder.
        """
        try:
            success_5m = float(self._metrics.success_rate(provider.provider_id, model))
        except Exception:  # pragma: no cover
            success_5m = None
        try:
            p50 = self._metrics.p50_ms(provider.provider_id, model)
        except Exception:  # pragma: no cover
            p50 = None
        try:
            current_conc = int(self._metrics.current_concurrency(provider.provider_id))
        except Exception:  # pragma: no cover
            current_conc = None
        try:
            current_rpm = int(self._metrics.recent_calls_in_60s(provider.provider_id))
        except Exception:  # pragma: no cover
            current_rpm = None

        circuit_state = "healthy"
        try:
            from app.domain.circuit_breaker import get_circuit_breaker

            # The breaker exposes ``get_state`` as async; we must not
            # await here (this is a sync helper) but we can read the
            # in-memory state struct via the cached path. Falling back
            # to the persisted column avoids a DB hit; misses just
            # default to ``healthy``.
            breaker = self._breaker if self._breaker else get_circuit_breaker()
            cached = getattr(breaker, "_states", {}).get(provider.provider_id)
            if cached is not None:
                circuit_state = getattr(cached, "state", "healthy")
        except Exception:  # pragma: no cover
            circuit_state = "healthy"

        return {
            "circuit_state": circuit_state,
            "success_rate_5m": success_5m,
            "p50_latency_ms": p50,
            "current_concurrency": current_conc,
            "max_concurrency": int(provider.max_concurrency or 0),
            "current_rpm": current_rpm,
            "rpm_limit": int(provider.rpm_limit or 0),
            "extra": {},
        }

    async def _mark_started(self, hash_id: str) -> None:
        async with get_session() as session:
            await session.execute(
                update(Job)
                .where(Job.hash_id == hash_id)
                .values(
                    started_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
            )

    async def _fail(
        self,
        ctx: JobExecutionContext,
        reason: str,
        *,
        refund_quota: bool,
    ) -> None:
        try:
            await self._lifecycle.transition(ctx.hash_id, FAILED, reason=reason)
        except InvalidTransition:
            logger.info("executor: job=%s could not fail as %s", ctx.hash_id, reason)
            return
        if refund_quota:
            await self._quota_guard.refund_usage(ctx.user.id)

    async def _publish_success_result(
        self,
        ctx: JobExecutionContext,
        *,
        image_payloads: list[dict[str, Any]],
        provider_id: str,
    ) -> None:
        """Best-effort richer result event for the future SSE hub."""
        try:
            await self._lifecycle.sink.broadcast_to_user(
                ctx.user.id,
                "job_state",
                {
                    "hash_id": ctx.hash_id,
                    "from": RUNNING,
                    "to": SUCCEEDED,
                    "ts": datetime.now(timezone.utc)
                    .isoformat(timespec="seconds")
                    .replace("+00:00", "Z"),
                    "result": {
                        "images": image_payloads,
                        "provider_used": provider_id,
                    },
                },
            )
        except Exception:
            logger.exception("executor: success-result broadcast failed")

    def _max_retries_per_job(self) -> int:
        try:
            return max(1, int(self._scoring.max_retries_per_job))
        except KeyError:
            return 3


def _store_one_image(
    hash_id: str,
    order: int,
    data: bytes,
    mime: str,
) -> dict[str, Any]:
    rel_original = image_io.save_original(hash_id, order, data, mime)
    original_abs = image_io.path_for_output_original(
        hash_id,
        order,
        image_io.mime_to_ext(mime),
    )
    thumb_abs = image_io.path_for_output_thumb(hash_id, order)
    image_io.make_thumbnail(original_abs, thumb_abs)
    width, height = _read_dimensions(original_abs)
    data_root = Path(get_settings().DATA_ROOT).resolve()
    return {
        "original_path": rel_original,
        "thumb_path": str(thumb_abs.relative_to(data_root)),
        "width": width,
        "height": height,
        "format": image_io.mime_to_ext(mime),
        "file_size_bytes": original_abs.stat().st_size,
    }


def _read_dimensions(path: Path) -> tuple[int, int]:
    with PILImage.open(path) as img:
        return img.size


def _safe_json_dict(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= 200 else value[:200] + "..."
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return str(value)


_instance: JobExecutor | None = None


def get_job_executor() -> JobExecutor:
    global _instance
    if _instance is None:
        _instance = JobExecutor()
    return _instance


def reset_job_executor_for_tests() -> None:
    global _instance
    _instance = None
