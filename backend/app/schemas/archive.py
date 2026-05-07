"""Pydantic shapes for ``app.api.archive`` — PR-14.

The archive surface is read-only from the user's perspective and is
designed to be cached in IndexedDB on the client. Three requirements
shape these schemas:

1. **Index endpoint.** ``GET /api/jobs/index`` returns one tiny row per
   job — just enough metadata for the frontend to decide whether to
   fetch full details. ``hash_id`` is the join key into the local
   cache; ``updated_at`` powers the ``?since=`` cursor; ``status``
   tells the cache which fields are still fluid.

2. **Detail endpoint.** ``GET /api/jobs/<hash>`` and the batch
   ``POST /api/jobs/details`` both return :class:`JobDetail`. We fold
   timing, references, images, set summary and the user-visible
   parameter set into a single shape so the right-side drawer can
   render in one render pass.

3. **States endpoint.** ``POST /api/jobs/states`` exists as a fallback
   for when the SSE channel is unavailable. It carries only the
   liveness fields (status / position / ETA) and is deliberately the
   leanest of the three so a 100-id batch still rounds in milliseconds.

All schemas use ``extra="forbid"`` so a stray field on the wire raises
422 at the boundary rather than silently propagating into the cache.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Shared field shapes
# ---------------------------------------------------------------------------


class JobImageSummary(BaseModel):
    """One generated image, as returned in :class:`JobDetail.images`.

    ``thumb_url`` is always populated for SUCCEEDED rows. For partial
    failures inside a Set (one cell of an n=4 didn't render) the row
    will be missing entirely — we don't fabricate placeholder rows.
    """

    model_config = ConfigDict(extra="forbid")

    image_id: str
    order: int
    thumb_url: str
    download_url: str
    width: int
    height: int
    format: str
    file_size_bytes: int
    starred: bool = False
    # Picker page state (PRD v1 §8.5.2). Mirrored from images.pick_state
    # so the Archive grid can show the same colour band as the picker.
    pick_state: str = "unjudged"


class JobReferenceSummary(BaseModel):
    """One reference image attached to the job at create time."""

    model_config = ConfigDict(extra="forbid")

    order: int
    filename: str
    thumb_url: str


class JobSetSummary(BaseModel):
    """When the job belongs to a Set (n>1) we expose the count + id.

    The Set itself isn't a separate resource — every job in the set
    shares the same ``set_id`` on its row and the archive aggregates by
    that id on the client. Carrying the count here saves the client a
    GROUP BY round-trip when rendering the Set tile.
    """

    model_config = ConfigDict(extra="forbid")

    set_id: str
    image_count: int


class JobSessionSummary(BaseModel):
    """When the job is bound to a session, expose the id + name."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str


class JobTiming(BaseModel):
    """Wall-clock breakdown — values are nullable until we hit them."""

    model_config = ConfigDict(extra="forbid")

    queued_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    queue_seconds: float | None = None
    render_seconds: float | None = None


# ---------------------------------------------------------------------------
# /api/jobs/index
# ---------------------------------------------------------------------------


class JobIndexEntry(BaseModel):
    """One row in the index list. Cheap enough to ship 1k of."""

    model_config = ConfigDict(extra="forbid")

    hash_id: str
    set_id: str | None = None
    seq_no: int
    status: str
    updated_at: datetime


class JobIndexResponse(BaseModel):
    """Reply for ``GET /api/jobs/index``.

    ``next_cursor`` is the ``hash_id`` of the last item when the page
    is full; ``None`` when the caller has caught up. The cursor is
    deliberately opaque to the frontend — we may later swap to a
    different ordering.
    """

    model_config = ConfigDict(extra="forbid")

    items: list[JobIndexEntry]
    next_cursor: str | None = None


# ---------------------------------------------------------------------------
# /api/jobs/<hash> and POST /api/jobs/details
# ---------------------------------------------------------------------------


class JobDetail(BaseModel):
    """Full user-visible detail for one job.

    Deliberately omits ``provider_used``, ``cost_cny`` and ``retries``
    (design doc §8.6) — those are admin-only fields. The right-side
    drawer renders this directly.
    """

    model_config = ConfigDict(extra="forbid")

    hash_id: str
    seq_no: int
    status: str
    status_reason: str | None = None
    model: str
    model_display_name: str

    # Mirrors the index entry so the frontend cache key stays consistent
    # whether the row arrived via /index or via /details. Without it the
    # client merge of (index row + detail row) would overwrite the
    # index-supplied updated_at with ``undefined``. Same reasoning for
    # the next two fields — they appear on the index entry but only
    # nested objects (``set``, ``session``) carry them on the detail
    # body, which doesn't survive a flat merge on the client.
    updated_at: datetime
    set_id: str | None = None
    session_id: str | None = None

    prompt: str
    params: dict[str, Any]

    references: list[JobReferenceSummary] = Field(default_factory=list)

    set: JobSetSummary | None = None
    images: list[JobImageSummary] = Field(default_factory=list)
    session: JobSessionSummary | None = None

    timing: JobTiming

    error: str | None = None
    flags: dict[str, Any] = Field(default_factory=dict)


class JobDetailNotFound(BaseModel):
    """Sentinel row used by ``POST /api/jobs/details`` for missing ids.

    The batch endpoint returns one entry per requested id. When the id
    is unknown (deleted / never existed / belongs to another user) we
    return this shape instead of :class:`JobDetail` so the frontend can
    align the response with its request without a separate 404 call.
    """

    model_config = ConfigDict(extra="forbid")

    hash_id: str
    not_found: bool = True


# Discriminated-friendly response wrapper. We can't use Pydantic's
# discriminated unions directly because :class:`JobDetail` doesn't
# carry a discriminator field — instead the route emits a list of
# either type and the client branches on the presence of ``not_found``.

class JobDetailsRequest(BaseModel):
    """Body for ``POST /api/jobs/details``."""

    model_config = ConfigDict(extra="forbid")

    hash_ids: list[str] = Field(..., min_length=1, max_length=50)


class JobDetailsResponse(BaseModel):
    """Reply for ``POST /api/jobs/details``.

    ``items`` order matches the request order. Use ``Any`` instead of a
    union to keep the discriminator handling on the client where the
    branch happens anyway; runtime validation is still enforced by the
    individual ``JobDetail`` / ``JobDetailNotFound`` constructors before
    they land in the list.
    """

    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# POST /api/jobs/states
# ---------------------------------------------------------------------------


class JobStatesRequest(BaseModel):
    """Body for ``POST /api/jobs/states``."""

    model_config = ConfigDict(extra="forbid")

    hash_ids: list[str] = Field(..., min_length=1, max_length=100)


class JobStateEntry(BaseModel):
    """One entry in the ``POST /api/jobs/states`` response.

    ``position`` and ``estimated_wait_seconds`` are populated only for
    QUEUED jobs; otherwise null. ``not_found`` is set when the id is
    unknown — same shape contract as :class:`JobDetailNotFound`.
    """

    model_config = ConfigDict(extra="forbid")

    hash_id: str
    status: str | None = None
    updated_at: datetime | None = None
    position: int | None = None
    estimated_wait_seconds: int | None = None
    not_found: bool = False


class JobStatesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[JobStateEntry]


# ---------------------------------------------------------------------------
# POST /api/jobs/<hash>/images/<order>/star
# ---------------------------------------------------------------------------


class StarRequest(BaseModel):
    """Body for the star toggle endpoint.

    Optional explicit ``starred`` value; when omitted the endpoint
    toggles the current state. Letting the client send the desired
    value avoids a stale-state race between the optimistic UI flip and
    the round-trip.
    """

    model_config = ConfigDict(extra="forbid")

    starred: bool | None = None


class StarResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hash_id: str
    order: int
    starred: bool


__all__ = (
    "JobImageSummary",
    "JobReferenceSummary",
    "JobSetSummary",
    "JobSessionSummary",
    "JobTiming",
    "JobIndexEntry",
    "JobIndexResponse",
    "JobDetail",
    "JobDetailNotFound",
    "JobDetailsRequest",
    "JobDetailsResponse",
    "JobStatesRequest",
    "JobStateEntry",
    "JobStatesResponse",
    "StarRequest",
    "StarResponse",
)
