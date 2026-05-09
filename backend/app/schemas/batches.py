"""Pydantic shapes for ``app.api.batches`` (frontend / backend doc v0.3).

The batch lifecycle has three logical surfaces:

1. **Registration**: ``POST /api/batches`` accepts a ``BatchCreateRequest``
   carrying a snapshot ``spec`` that describes — for human readers and
   for the detail drawer — what the user is about to submit. The body
   does not duplicate per-job fields (prompt, refs); those still ride
   each ``POST /api/jobs`` individually.

2. **Listing / detail**: ``GET /api/batches`` returns a paginated list of
   ``BatchListEntry`` rows; ``GET /api/batches/<id>`` returns one
   ``BatchDetail`` enriched with per-slot progress.

3. **Lifecycle ops**: ``finalize_submission`` / ``cancel`` / ``DELETE``
   share the lightweight ``BatchActionResponse`` (id + status pair).

Naming is intentionally aligned with the doc verbatim so the frontend
generator emits a matching ``Batches.ts`` if we ever need one.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Format regexes
# ---------------------------------------------------------------------------

_BATCH_ID_RE = re.compile(r"^bat_[A-Za-z0-9]{10}$")
_SET_ID_RE = re.compile(r"^set_[A-Za-z0-9]{10}$")
_SESSION_ID_RE = re.compile(r"^sess_[A-Za-z0-9]{10}$")
_J_HASH_ID_RE = re.compile(r"^j_[A-Za-z0-9]{1,64}$")


# ---------------------------------------------------------------------------
# spec_json shape
# ---------------------------------------------------------------------------


class BatchSlotSpec(BaseModel):
    """One slot inside the registration spec.

    Mirrors the v0.3 frontend doc §1.3. Carries enough metadata for the
    detail drawer to render without re-querying every Job; the bulk
    payload (prompt, refs) is per-Job and lands on disk via
    ``POST /api/jobs``.
    """

    model_config = ConfigDict(extra="forbid")

    stable_idx: int = Field(..., ge=1, le=999)
    title: str = Field(..., min_length=0, max_length=200)
    prompt_summary: str = Field(default="", max_length=200)
    image_count: int = Field(..., ge=1, le=16)
    set_id: str | None = None
    session_id: str | None = None

    @field_validator("set_id")
    @classmethod
    def _validate_set_id(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not _SET_ID_RE.match(v):
            raise ValueError("set_id must match ^set_[A-Za-z0-9]{10}$")
        return v

    @field_validator("session_id")
    @classmethod
    def _validate_session_id(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not _SESSION_ID_RE.match(v):
            raise ValueError("session_id must match ^sess_[A-Za-z0-9]{10}$")
        return v


class BatchSpec(BaseModel):
    """Snapshot of the editor at submit time.

    Stored as JSON in ``batches.spec_json``. Read back when the detail
    drawer asks for the batch — gives the renderer a stable view of what
    the user submitted, even after Jobs are deleted from archive.
    """

    model_config = ConfigDict(extra="forbid")

    fixed_prompt_summary: str = Field(default="", max_length=200)
    fixed_ref_count: int = Field(default=0, ge=0, le=14)
    session_strategy: Literal[
        "per_slot_new", "batch_shared_new", "existing_session", "none"
    ] = "per_slot_new"
    shared_session_id: str | None = None
    slots: list[BatchSlotSpec] = Field(default_factory=list)

    @field_validator("shared_session_id")
    @classmethod
    def _validate_shared_session_id(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not _SESSION_ID_RE.match(v):
            raise ValueError("shared_session_id must match ^sess_[A-Za-z0-9]{10}$")
        return v

    @model_validator(mode="after")
    def _check_slots_cap(self) -> "BatchSpec":
        if len(self.slots) > 50:
            raise ValueError("a batch can hold at most 50 slots")
        # Each stable_idx must be unique inside the batch — frontend
        # uses it as the React key and to address cards in SSE updates.
        seen: set[int] = set()
        for slot in self.slots:
            if slot.stable_idx in seen:
                raise ValueError(
                    f"duplicate stable_idx {slot.stable_idx} in slots"
                )
            seen.add(slot.stable_idx)
        return self


# ---------------------------------------------------------------------------
# POST /api/batches
# ---------------------------------------------------------------------------


class BatchCreateRequest(BaseModel):
    """Body for ``POST /api/batches`` (registration / stage ①)."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=120)
    total_job_count: int = Field(..., ge=1, le=400)
    spec: BatchSpec

    @model_validator(mode="after")
    def _check_total_consistency(self) -> "BatchCreateRequest":
        derived = sum(s.image_count for s in self.spec.slots)
        if derived != self.total_job_count:
            raise ValueError(
                "total_job_count must equal Σ slots[].image_count "
                f"({derived} != {self.total_job_count})"
            )
        return self


# ---------------------------------------------------------------------------
# Response shapes
# ---------------------------------------------------------------------------


BatchStatus = Literal[
    "submitting",
    "running",
    "completed",
    "partial",
    "cancelled",
    "abandoned",
]


class BatchSummary(BaseModel):
    """Common projection — used by registration response + list rows.

    Lightweight: omits ``spec`` and per-slot data so a list of 200
    historical batches is cheap to serialise.
    """

    model_config = ConfigDict(extra="forbid")

    batch_id: str
    title: str
    status: BatchStatus
    total_job_count: int
    submitted_count: int
    succeeded_count: int
    failed_count: int
    cancelled_count: int
    created_at: datetime
    updated_at: datetime
    last_activity_at: datetime
    finalized_at: datetime | None = None


class BatchCreateResponse(BatchSummary):
    """Reply for ``POST /api/batches``.

    Includes the parsed back ``spec`` so the client can stash it for the
    detail drawer without an extra round-trip.
    """

    spec: BatchSpec


class BatchListResponse(BaseModel):
    """Reply for ``GET /api/batches``."""

    model_config = ConfigDict(extra="forbid")

    items: list[BatchSummary]
    next_cursor: str | None = None


class BatchSlotProgress(BaseModel):
    """Aggregated slot view inside the detail drawer.

    Driven by a single ``GROUP BY set_id, status`` over jobs filtered
    on ``batch_id == :id``. ``job_hash_ids`` is the ordered list of
    bound jobs; the frontend uses it to wire each row to its archive /
    picker entry.
    """

    model_config = ConfigDict(extra="forbid")

    stable_idx: int
    title: str
    set_id: str | None = None
    session_id: str | None = None
    image_count: int
    succeeded: int
    failed: int
    cancelled: int
    in_flight: int
    queued: int
    job_hash_ids: list[str]


class BatchDetail(BatchSummary):
    """Reply for ``GET /api/batches/<id>``."""

    spec: BatchSpec
    in_flight_count: int
    slots: list[BatchSlotProgress]


class BatchActionResponse(BaseModel):
    """Reply for finalize / cancel / DELETE — shared shape."""

    model_config = ConfigDict(extra="forbid")

    batch_id: str
    status: BatchStatus
    cancelled_now_count: int | None = None


# ---------------------------------------------------------------------------
# Validators (re-exported for tests)
# ---------------------------------------------------------------------------


def is_valid_batch_id(value: str) -> bool:
    return bool(_BATCH_ID_RE.match(value or ""))


__all__ = (
    "BatchSlotSpec",
    "BatchSpec",
    "BatchCreateRequest",
    "BatchSummary",
    "BatchCreateResponse",
    "BatchListResponse",
    "BatchSlotProgress",
    "BatchDetail",
    "BatchActionResponse",
    "BatchStatus",
    "is_valid_batch_id",
)
