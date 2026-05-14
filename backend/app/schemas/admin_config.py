"""Pydantic shapes for admin config + tier endpoints.

Kept separate from the auth schemas so a frontend type generator can
emit a tidy ``adminConfig.ts`` module mirroring this file.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Config (key-value store under app.domain.config_center)
# ---------------------------------------------------------------------------


class ConfigUpdateResponse(BaseModel):
    """Echo of accepted updates after type coercion."""

    model_config = ConfigDict(extra="forbid")
    updated: dict[str, Any]


# ---------------------------------------------------------------------------
# Tiers
# ---------------------------------------------------------------------------


class TierResponse(BaseModel):
    """One tier row as returned by ``GET /api/admin/tiers``.

    Mirrors design doc §3.1 / §14.2 verbatim. The values come straight
    from the DB so admin tools can round-trip them without any client-
    side reshuffling.
    """

    tier: str
    weight: int
    max_concurrency: int
    max_queue: int
    soft_quota: int
    hard_quota: int
    slo_p95_ms: int | None = None
    burst_limit: int


class TierListResponse(BaseModel):
    tiers: list[TierResponse]


class TierPatchRequest(BaseModel):
    """All fields optional — admin patches a subset.

    ``slo_p95_ms`` accepts ``null`` explicitly so an admin can clear
    the SLO target on, say, the Free tier without using a sentinel.
    Pydantic's default behaviour treats omission and ``null`` the same;
    we distinguish them with ``model_fields_set`` in the handler.
    """

    model_config = ConfigDict(extra="forbid")

    weight: int | None = Field(default=None, ge=1, le=1024)
    max_concurrency: int | None = Field(default=None, ge=1, le=1024)
    max_queue: int | None = Field(default=None, ge=0, le=10_000)
    soft_quota: int | None = Field(default=None, ge=0, le=1_000_000)
    hard_quota: int | None = Field(default=None, ge=0, le=1_000_000)
    slo_p95_ms: int | None = Field(default=None, ge=1, le=24 * 3600 * 1000)
    # Per-tier rolling-window threshold for the anti-abuse burst gate
    # (``_recent_burst``). Lower bound 1 so accidentally setting 0
    # doesn't disable the gate entirely; upper bound is the per-tier
    # hard_quota practical ceiling (no point allowing more burst than
    # the user's daily hard cap).
    burst_limit: int | None = Field(default=None, ge=1, le=1_000)
