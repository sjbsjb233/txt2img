"""Aggregate router for ``/api/me/*`` endpoints (user-self surface).

Backs the ``/settings`` page in the frontend. Each submodule owns one
slice of the surface so the file size stays small and audit / rate-limit
helpers can be shared via :mod:`app.api.me.helpers`.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.me.deletion import router as deletion_router
from app.api.me.password import router as password_router
from app.api.me.preferences import router as preferences_router
from app.api.me.profile import router as profile_router
from app.api.me.sessions import router as sessions_router

router = APIRouter()
router.include_router(profile_router)
router.include_router(preferences_router)
router.include_router(password_router)
router.include_router(sessions_router)
router.include_router(deletion_router)
