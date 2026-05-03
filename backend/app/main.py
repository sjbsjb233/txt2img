"""FastAPI application entrypoint.

PR-01 scope: skeleton only — wires up the lifespan, CORS, and the health
route. Auth, jobs, models, archive, admin and SSE routers are added in
later PRs as their corresponding domain services come online.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from contextlib import suppress
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.adapters.base import AdapterRegistry
from app.api.admin.adapters import router as admin_adapters_router
from app.api.admin.announcements import router as admin_announcements_router
from app.api.admin.approvals import router as admin_approvals_router
from app.api.admin.audit import router as admin_audit_router
from app.api.admin.cleanup import router as admin_cleanup_router
from app.api.admin.config import router as admin_config_router
from app.api.admin.metrics import router as admin_metrics_router
from app.api.admin.providers import router as admin_providers_router
from app.api.admin.sse import router as admin_sse_router
from app.api.admin.tiers import router as admin_tiers_router
from app.api.admin.users import router as admin_users_router
from app.api.announcements import router as announcements_router
from app.api.archive import router as archive_router
from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.jobs import router as jobs_router
from app.api.me import router as me_router
from app.api.models import router as models_router
from app.api.sessions import router as sessions_router
from app.api.sse import router as sse_router
from app.config import get_settings
from app.db import engine as db_engine
from app.db import seed as db_seed
from app.db.migrate import upgrade_to_head
from app.domain.admin_broadcaster import (
    collect_worker_pool_snapshot,
    run_provider_metrics_loop,
    run_worker_pool_loop,
)
from app.domain.account_lifecycle import run_account_lifecycle_loop
from app.domain.cache_keeper import run_disk_usage_loop
from app.domain.circuit_breaker import get_circuit_breaker
from app.domain.config_center import get_config_center
from app.domain.graceful_shutdown import force_fail_running_jobs
from app.domain.job_executor import get_job_executor
from app.domain.job_lifecycle import set_broadcast_sink
from app.domain.job_queue import get_job_queue
from app.domain.job_scheduler import (
    get_job_scheduler,
    list_provider_ids,
    restore_queued_jobs,
)
from app.domain.metrics_engine import get_metrics_engine, run_metrics_snapshot_loop
from app.domain.model_catalog import validate_primary_adapters
from app.domain.provider_ledger import get_provider_ledger
from app.domain.provider_selector import get_provider_selector
from app.domain.quota_guard import run_quota_reset_loop
from app.domain.sse_hub import get_sse_hub
from app.domain.tier_config import get_tier_config
from app.utils.crypto import CryptoError

logger = logging.getLogger("txt2img")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup / shutdown hook.

    Per design doc §15.2 this is the single place where process-wide
    singletons get wired up. PR-02 adds the bottom of the stack: ensure
    the data directory exists, run migrations, init the SQLAlchemy engine,
    and seed defaults. Later PRs slot config center, scheduler, SSE hub
    and background tasks in here in dependency order.
    """
    settings = get_settings()
    print(f"[txt2img] starting backend version={settings.APP_VERSION}", flush=True)
    logger.info("starting txt2img backend version=%s", settings.APP_VERSION)
    try:
        os.makedirs(settings.DATA_ROOT, exist_ok=True)
    except OSError as exc:
        logger.warning("could not ensure DATA_ROOT %s: %s", settings.DATA_ROOT, exc)

    # Migrations run synchronously against the sync sqlite driver and must
    # complete before init_engine opens an aiosqlite connection — otherwise
    # the engine would see an empty DB and the seed below would fail.
    upgrade_to_head()
    db_engine.init_engine()
    await db_seed.bootstrap()

    # Hydrate the in-memory config caches before any route handler runs.
    # Both reads must succeed: the scheduler / access policy / etc. that
    # PR-09+ wire on top assume both caches are warm at startup.
    await get_config_center().load_from_db()
    await get_tier_config().load_from_db()

    # Adapter discovery has to happen after the DB is up because the admin
    # listing route joins against ``providers``, but it has no I/O of its
    # own — just imports the modules under ``app.adapters/`` and
    # instantiates each non-abstract subclass. Re-running ``discover`` on
    # an already-populated registry is a no-op (duplicates are skipped).
    AdapterRegistry.instance().discover()
    # Warn if any model in ``_MODEL_DISPLAY`` declares a primary_adapter
    # that's missing or doesn't list the model — symptom is an empty
    # Create-page parameter panel for that model.
    validate_primary_adapters()

    metrics = get_metrics_engine()
    breaker = get_circuit_breaker()
    selector = get_provider_selector()
    ledger = get_provider_ledger()
    queue = get_job_queue()
    executor = get_job_executor()
    scheduler = get_job_scheduler()
    # Wire the SSE hub before the scheduler starts so the very first
    # ``QUEUED → RUNNING`` transition the executor performs already
    # finds a real fanout target. With the default ``NullBroadcastSink``
    # the lifecycle would silently drop ``job_state`` events between
    # process start and whenever we'd otherwise install the sink.
    sse_hub = get_sse_hub()
    set_broadcast_sink(sse_hub)
    app.state.metrics = metrics
    app.state.breaker = breaker
    app.state.selector = selector
    app.state.ledger = ledger
    app.state.queue = queue
    app.state.executor = executor
    app.state.scheduler = scheduler
    app.state.sse_hub = sse_hub

    restored = await restore_queued_jobs(queue)
    if restored:
        logger.info("restored %d queued job(s) into scheduler", restored)

    # Reconcile any RUNNING rows left behind by a previous unclean exit.
    # Without this sweep, jobs that were mid-flight when the process was
    # killed would stay RUNNING forever — no executor task owns them
    # anymore, so they would never reach a terminal state on their own.
    # The sweep refunds quota for each forced FAILED, so a user who lost
    # an in-flight job to a crash isn't punished for it. Per design doc
    # §20 problem 7.
    crashed = await force_fail_running_jobs()
    if crashed:
        logger.warning(
            "startup: reconciled %d RUNNING job(s) from previous unclean exit",
            crashed,
        )

    app.state.quota_reset_task = asyncio.create_task(run_quota_reset_loop())
    app.state.metrics_snapshot_task = asyncio.create_task(
        run_metrics_snapshot_loop(metrics, list_provider_ids)
    )
    app.state.scheduler_task = asyncio.create_task(scheduler.run_forever(executor))
    # PR-16: periodic disk-usage rollup so the cleanup suggestions
    # surface fresh numbers without an admin manually triggering a
    # walk; admin metrics + worker-pool fanout so admin dashboards
    # stay live without polling. All three are best-effort and never
    # die on transient errors — the loops swallow exceptions.
    app.state.disk_usage_task = asyncio.create_task(run_disk_usage_loop())
    # Account-deletion lifecycle: T+7 → soft delete, T+30 → purge.
    # Runs every 6h; cheap on a tiny SQLite. See
    # ``app.domain.account_lifecycle`` for the policy.
    app.state.account_lifecycle_task = asyncio.create_task(
        run_account_lifecycle_loop()
    )
    app.state.admin_metrics_task = asyncio.create_task(
        run_provider_metrics_loop(metrics, sse_hub)
    )
    app.state.admin_pool_task = asyncio.create_task(
        run_worker_pool_loop(collect_worker_pool_snapshot, sse_hub)
    )

    try:
        yield
    finally:
        print("[txt2img] closing backend", flush=True)
        logger.info("closing txt2img backend")
        await scheduler.drain(timeout=30)
        # Anything still in RUNNING after drain is a job whose worker
        # did not finish in the 30s window. We force-fail those rows
        # so users do not see their counters held forever and so the
        # archive UI eventually shows a terminal state instead of a
        # spinner that never resolves. Refund happens inside the
        # helper, clamped at zero by QuotaGuard.
        try:
            forced = await force_fail_running_jobs()
            if forced:
                logger.warning(
                    "shutdown: force-failed %d still-RUNNING job(s) "
                    "after drain timeout",
                    forced,
                )
        except Exception:
            logger.exception("shutdown: force-fail sweep failed; continuing")
        scheduler_task = getattr(app.state, "scheduler_task", None)
        if scheduler_task is not None:
            scheduler_task.cancel()
            with suppress(asyncio.CancelledError):
                await scheduler_task
        metrics_task = getattr(app.state, "metrics_snapshot_task", None)
        if metrics_task is not None:
            metrics_task.cancel()
            with suppress(asyncio.CancelledError):
                await metrics_task
        quota_task = getattr(app.state, "quota_reset_task", None)
        if quota_task is not None:
            quota_task.cancel()
            with suppress(asyncio.CancelledError):
                await quota_task
        for attr in (
            "disk_usage_task",
            "admin_metrics_task",
            "admin_pool_task",
            "account_lifecycle_task",
        ):
            t = getattr(app.state, attr, None)
            if t is not None:
                t.cancel()
                with suppress(asyncio.CancelledError):
                    await t
        await db_engine.close_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="txt2img backend",
        version=settings.APP_VERSION,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Surface a corrupted ``providers.api_key_enc`` row as a clean 500
    # rather than letting the raw exception escape past starlette's
    # exception middleware. Per ``app/utils/crypto.py``: a row whose
    # ciphertext we can't decrypt is broken state we cannot paper
    # over. The 500 message is generic — operators look at the log
    # for the offending provider id (logged by ``_view_from_parts``).
    @app.exception_handler(CryptoError)
    async def _crypto_error_handler(  # type: ignore[unused-ignore]
        request: Request, exc: CryptoError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={
                "detail": {
                    "code": "CRYPTO_ERROR",
                    "message": (
                        "A stored secret could not be decrypted. "
                        "Check JWT_SECRET and the affected provider row."
                    ),
                    "field": None,
                    "extra": None,
                }
            },
        )

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(me_router)
    app.include_router(sse_router)
    app.include_router(sessions_router)
    app.include_router(models_router)
    app.include_router(jobs_router)
    # Archive routes share the ``/api/jobs`` URL prefix with jobs_router.
    # Both routers register distinct paths (jobs.py owns the action
    # routes — POST/cancel/delete; archive.py owns the read paths —
    # GET detail / index / batch / image streams / star). Register
    # archive *after* jobs so the more specific patterns from jobs
    # (e.g. POST /api/jobs/precheck) win first-match.
    app.include_router(archive_router)
    app.include_router(announcements_router)
    app.include_router(admin_adapters_router)
    app.include_router(admin_announcements_router)
    app.include_router(admin_approvals_router)
    app.include_router(admin_audit_router)
    app.include_router(admin_cleanup_router)
    app.include_router(admin_config_router)
    app.include_router(admin_metrics_router)
    app.include_router(admin_providers_router)
    app.include_router(admin_sse_router)
    app.include_router(admin_tiers_router)
    app.include_router(admin_users_router)
    return app


app = create_app()
