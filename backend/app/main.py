"""FastAPI application entrypoint.

PR-01 scope: skeleton only — wires up the lifespan, CORS, and the health
route. Auth, jobs, models, archive, admin and SSE routers are added in
later PRs as their corresponding domain services come online.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.adapters.base import AdapterRegistry
from app.api.admin.adapters import router as admin_adapters_router
from app.api.admin.config import router as admin_config_router
from app.api.admin.providers import router as admin_providers_router
from app.api.admin.tiers import router as admin_tiers_router
from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.sessions import router as sessions_router
from app.config import get_settings
from app.db import engine as db_engine
from app.db import seed as db_seed
from app.db.migrate import upgrade_to_head
from app.domain.config_center import get_config_center
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

    try:
        yield
    finally:
        print("[txt2img] closing backend", flush=True)
        logger.info("closing txt2img backend")
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
    app.include_router(sessions_router)
    app.include_router(admin_adapters_router)
    app.include_router(admin_config_router)
    app.include_router(admin_providers_router)
    app.include_router(admin_tiers_router)
    return app


app = create_app()
