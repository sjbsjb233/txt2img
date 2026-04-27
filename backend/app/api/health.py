"""Liveness probe.

Public, unauthenticated endpoint that load balancers, Watchtower and
monitoring agents can poll. The payload shape is part of the public contract
documented in the design doc §16.1, so don't reshape it without bumping the
version field.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.config import get_settings

router = APIRouter()


@router.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": get_settings().APP_VERSION}
