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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.config import get_settings
from app.db import engine as db_engine
from app.db import seed as db_seed
from app.db.migrate import upgrade_to_head

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
    app.include_router(health_router)
    app.include_router(auth_router)
    return app


app = create_app()
