import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .init_db import init as init_db
from .routers import auth as auth_router
from .routers import users as users_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="txt2img backend", version="0.1.0", lifespan=lifespan)

# Lenient mode: when CORS_ORIGINS contains "*", allow any origin via regex.
# A literal "*" with allow_credentials=True is rejected by browsers, so we
# route through allow_origin_regex instead.
_origins = settings.cors_origins_list
_allow_any = "*" in _origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=[] if _allow_any else _origins,
    allow_origin_regex=".*" if _allow_any else None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


app.include_router(auth_router.router, prefix="/api")
app.include_router(users_router.router, prefix="/api")
