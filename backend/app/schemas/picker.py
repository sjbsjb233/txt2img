"""Pydantic shapes for ``app.api.picker``.

The Picker page (PRD v1) ships three classes of requests / responses:

1. **Per-image judgments** — ``POST /api/jobs/<hash>/images/<order>/{pick,
   discard,final,defer,unjudge}``. All five reuse :class:`PickStateResponse`
   so the frontend has a single shape to merge into its store regardless
   of which transition was made.
2. **Session-level reads** — ``GET /api/sessions/<id>/picker`` returns
   :class:`SessionPickerResponse`, the one-shot bundle the judging page
   needs (jobs + images + session metadata in one round trip per the
   PRD's "no N+1 requests" constraint).
3. **Deck overview** — ``GET /api/picker/overview`` returns
   :class:`DeckOverviewResponse`, the entry-page dashboard that summarises
   every session.

Every model uses ``extra="forbid"`` so a stray field on the wire raises
422 at the boundary rather than silently propagating into the cache.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


PickStateLiteral = Literal[
    "unjudged", "picked", "discarded", "final", "deferred"
]
SessionPickerStateLiteral = Literal["not_started", "judging", "finalized"]
JobStatusLiteral = Literal[
    "QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"
]


# ---------------------------------------------------------------------------
# Per-image judgment
# ---------------------------------------------------------------------------


class PickStateResponse(BaseModel):
    """Response for every per-image write endpoint.

    ``previous_final_image_id`` is only populated by the ``final`` route
    when it bumped a sibling out of the FINAL slot — in every other
    case it stays ``None``. The frontend uses it to drive the local
    "old final → picked" patch without a second SSE roundtrip on the
    same tab.
    """

    model_config = ConfigDict(extra="forbid")

    image_id: str
    hash_id: str
    order: int
    pick_state: PickStateLiteral
    pick_state_updated_at: datetime
    session_id: str | None
    session_picker_state: SessionPickerStateLiteral
    session_final_image_id: str | None
    previous_final_image_id: str | None = None
    starred: bool


# ---------------------------------------------------------------------------
# /api/sessions/<id>/picker — judging-page bundle
# ---------------------------------------------------------------------------


class PickerImage(BaseModel):
    """One image as the picker page sees it.

    Carries only the fields the judging UI needs in the first paint —
    no upstream metadata, no provider details. ``seed`` and ``job_idx``
    are denormalised from the parent job so the frontend doesn't have
    to walk the jobs[] list to render the meta-rail.
    """

    model_config = ConfigDict(extra="forbid")

    image_id: str
    hash_id: str
    order: int
    thumb_url: str
    download_url: str
    pick_state: PickStateLiteral
    pick_state_updated_at: datetime | None = None
    starred: bool
    width: int
    height: int
    seed: str | None = None
    job_idx: int


class PickerJob(BaseModel):
    """One job summary as the picker meta-rail sees it.

    Includes all jobs in the session — both terminal and in-flight —
    so the page can render the in-flight tray without a second call.
    Position / ETA / runStartedAt mirror what SSE would push and are
    populated only for QUEUED / RUNNING rows.
    """

    model_config = ConfigDict(extra="forbid")

    hash_id: str
    status: JobStatusLiteral
    model: str
    model_display_name: str
    prompt: str
    params: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    position: int | None = None
    estimated_wait_seconds: int | None = None
    error: str | None = None


class PickerSessionMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    picker_state: SessionPickerStateLiteral
    final_image_id: str | None
    cursor_image_id: str | None
    finalized_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SessionPickerResponse(BaseModel):
    """One-shot bundle returned by ``GET /api/sessions/<id>/picker``."""

    model_config = ConfigDict(extra="forbid")

    session: PickerSessionMeta
    jobs: list[PickerJob] = Field(default_factory=list)
    images: list[PickerImage] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# /api/picker/overview — deck dashboard
# ---------------------------------------------------------------------------


class DeckSessionStats(BaseModel):
    """Per-state tally for one session in the deck overview."""

    model_config = ConfigDict(extra="forbid")

    final: int = 0
    picked: int = 0
    discarded: int = 0
    deferred: int = 0
    unjudged: int = 0


class DeckSessionInFlight(BaseModel):
    """In-flight job tally per status."""

    model_config = ConfigDict(extra="forbid")

    queued: int = 0
    running: int = 0
    failed: int = 0


class DeckSessionSummary(BaseModel):
    """One session card on the deck-overview entry page."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    picker_state: SessionPickerStateLiteral
    image_count: int
    judged_count: int
    stats: DeckSessionStats
    in_flight: DeckSessionInFlight
    final_image_id: str | None = None
    final_thumb_url: str | None = None
    last_prompt: str | None = None
    updated_at: datetime
    created_at: datetime
    finalized_at: datetime | None = None


class DeckOverviewTotals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    images: int = 0
    judged: int = 0
    inflight: int = 0


class DeckOverviewSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finalized: int = 0
    ready_to_finalize: int = 0
    judging: int = 0
    not_started: int = 0


class DeckOverviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deck_title: str
    last_edited: datetime | None = None
    totals: DeckOverviewTotals
    summary: DeckOverviewSummary
    sessions: list[DeckSessionSummary] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Session writes
# ---------------------------------------------------------------------------


class SessionCursorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cursor_image_id: str | None = None


class SessionCursorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    cursor_image_id: str | None


class SessionFinalizeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    picker_state: SessionPickerStateLiteral
    finalized_at: datetime | None
    final_image_id: str | None


__all__ = (
    "PickStateLiteral",
    "SessionPickerStateLiteral",
    "JobStatusLiteral",
    "PickStateResponse",
    "PickerImage",
    "PickerJob",
    "PickerSessionMeta",
    "SessionPickerResponse",
    "DeckSessionStats",
    "DeckSessionInFlight",
    "DeckSessionSummary",
    "DeckOverviewTotals",
    "DeckOverviewSummary",
    "DeckOverviewResponse",
    "SessionCursorRequest",
    "SessionCursorResponse",
    "SessionFinalizeResponse",
)
