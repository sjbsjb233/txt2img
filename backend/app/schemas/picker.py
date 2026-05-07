"""Pydantic shapes for ``app.api.picker`` — Picker page (PRD §9).

Three families of shapes:

1. Per-image judgment write responses — the five state transition
   endpoints (pick/discard/final/defer/unjudge) all return the same
   shape (a ``JudgmentResponse``); the F endpoint additionally surfaces
   ``previous_final_image_id`` for the swap case.
2. Session-level operations — finalize/unfinalize/cursor + the
   per-session and global picker reads.
3. Vary-seed: a tiny request body for ``POST /api/jobs/vary``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


PickState = Literal["unjudged", "picked", "discarded", "final", "deferred"]
PickerState = Literal["not_started", "judging", "finalized"]
JobStatus = Literal[
    "QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED", "DELETED"
]


# ---------------------------------------------------------------------------
# Per-image judgment writes
# ---------------------------------------------------------------------------


class JudgmentResponse(BaseModel):
    """Response for a single image-state write.

    Mirrors PRD §9.1. We intentionally include both the image-level
    state and the recomputed session-level fields so the client can
    update its store from one round-trip without a follow-up read.
    """

    model_config = ConfigDict(extra="forbid")

    image_id: str
    hash_id: str
    order: int
    pick_state: PickState
    pick_state_updated_at: datetime
    starred: bool

    session_id: str | None = None
    session_picker_state: PickerState | None = None
    session_final_image_id: str | None = None

    # Only populated on the final-set / final-unset paths so the client
    # can also update the previous final's row in its cache.
    previous_final_image_id: str | None = None


# ---------------------------------------------------------------------------
# Session finalize / unfinalize / cursor
# ---------------------------------------------------------------------------


class CursorPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cursor_image_id: str | None = None


class CursorPatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    cursor_image_id: str | None


class FinalizeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    picker_state: PickerState
    finalized_at: datetime | None
    final_image_id: str | None


# ---------------------------------------------------------------------------
# Per-session picker view
# ---------------------------------------------------------------------------


class PickerJobView(BaseModel):
    """One job under a session, with execution metadata.

    Includes only the fields the picker actually needs (status, prompt,
    timing). Additional details — model display name, params — live on
    the existing ``JobDetail`` and are not duplicated here.
    """

    model_config = ConfigDict(extra="forbid")

    hash_id: str
    status: JobStatus
    model: str
    model_display_name: str
    prompt: str
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class PickerImageView(BaseModel):
    """One image with picker-specific fields baked in.

    Different from ``JobImageSummary`` (archive) because the picker
    needs ``pick_state`` + ``seed`` + ``job_idx`` exposed to render the
    Image rail without a join.
    """

    model_config = ConfigDict(extra="forbid")

    image_id: str
    hash_id: str
    order: int
    thumb_url: str
    download_url: str
    width: int
    height: int
    pick_state: PickState
    pick_state_updated_at: datetime | None = None
    starred: bool

    seed: str | None = None
    job_idx: int


class PickerSessionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    picker_state: PickerState
    final_image_id: str | None
    cursor_image_id: str | None
    finalized_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PickerSessionResponse(BaseModel):
    """``GET /api/sessions/<id>/picker`` aggregated payload."""

    model_config = ConfigDict(extra="forbid")

    session: PickerSessionView
    jobs: list[PickerJobView] = Field(default_factory=list)
    images: list[PickerImageView] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Deck overview (/api/picker/overview)
# ---------------------------------------------------------------------------


class PickerSessionStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    final: int = 0
    picked: int = 0
    discarded: int = 0
    deferred: int = 0
    unjudged: int = 0


class PickerInFlight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queued: int = 0
    running: int = 0
    failed: int = 0


class PickerOverviewSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    picker_state: PickerState
    image_count: int
    judged_count: int
    stats: PickerSessionStats
    in_flight: PickerInFlight
    final_image_id: str | None = None
    final_thumb_url: str | None = None
    last_prompt: str | None = None
    updated_at: datetime


class PickerOverviewTotals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    images: int = 0
    judged: int = 0
    inflight: int = 0


class PickerOverviewSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finalized: int = 0
    ready_to_finalize: int = 0
    judging: int = 0
    not_started: int = 0


class PickerOverviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deck_title: str = "Untitled deck"
    last_edited: datetime | None = None
    totals: PickerOverviewTotals
    summary: PickerOverviewSummary
    sessions: list[PickerOverviewSession] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Vary seed
# ---------------------------------------------------------------------------


class VarySeedRequest(BaseModel):
    """Body for ``POST /api/jobs/vary`` (PRD §6.8)."""

    model_config = ConfigDict(extra="forbid")

    source_image_id: str
    seed: int | str | None = None


__all__ = (
    "PickState",
    "PickerState",
    "JudgmentResponse",
    "CursorPatchRequest",
    "CursorPatchResponse",
    "FinalizeResponse",
    "PickerJobView",
    "PickerImageView",
    "PickerSessionView",
    "PickerSessionResponse",
    "PickerSessionStats",
    "PickerInFlight",
    "PickerOverviewSession",
    "PickerOverviewTotals",
    "PickerOverviewSummary",
    "PickerOverviewResponse",
    "VarySeedRequest",
)
