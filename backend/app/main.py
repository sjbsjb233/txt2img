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

from app.api.health import router as health_router
from app.config import get_settings

logger = logging.getLogger("txt2img")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup / shutdown hook.

    Currently a placeholder. Subsequent PRs initialize the DB engine,
    config center, scheduler, SSE hub and background tasks here in the
    order described in the design doc §15.2.
    """
    settings = get_settings()
    print(f"[txt2img] starting backend version={settings.APP_VERSION}", flush=True)
    logger.info("starting txt2img backend version=%s", settings.APP_VERSION)
    try:
        os.makedirs(settings.DATA_ROOT, exist_ok=True)
    except OSError as exc:
        logger.warning("could not ensure DATA_ROOT %s: %s", settings.DATA_ROOT, exc)
    try:
        yield
    finally:
        print("[txt2img] closing backend", flush=True)
        logger.info("closing txt2img backend")


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
    return app


app = create_app()
