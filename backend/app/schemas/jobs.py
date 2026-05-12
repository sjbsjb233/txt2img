"""Pydantic shapes for ``app.api.jobs`` — PR-13.

Three request bodies and three response bodies, all matching design doc
§6.3 / §6.4 / §16.2 / §17. Field naming follows the design doc literally
so the frontend generator can emit a matching ``jobs.ts``.

Why a dedicated module
----------------------
The Create page contract crosses several backend boundaries (admission
policy, parameter validation, queue admission, SSE broadcast). Putting
the wire shapes in one file means:

- One read for the frontend integrator.
- The parameter validator (``app.api.jobs._validate_against_capabilities``)
  consumes :class:`JobCreatePayload` directly without round-tripping
  through ``NormalizedRequest`` first.
- ``client_request_id`` is a contract-level idempotency knob; declaring
  it here keeps the dedup logic visible in the route handler.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# Models the caller can request. Adapter / provider validation is the
# authoritative gate; this regex only constrains the URL/log-friendly
# shape so a malformed value doesn't propagate further.
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,127}$")
_SESSION_ID_RE = re.compile(r"^sess_[A-Za-z0-9]{10}$")
_BATCH_ID_RE = re.compile(r"^bat_[A-Za-z0-9]{10}$")
_SET_ID_RE = re.compile(r"^set_[A-Za-z0-9]{10}$")
_CLIENT_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
# Public hash id format (from new_job_hash_id).
_HASH_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
_OUTPAINT_AMOUNT_RE = re.compile(r"^(\d{1,4}%|\d{1,5}px)$")


class DerivationKind(str, Enum):
    """How a child job is derived from a parent job."""

    MASK_EDIT = "mask_edit"
    OUTPAINT = "outpaint"
    IMAGE_TO_IMAGE = "i2i"


class OutpaintDirection(str, Enum):
    LEFT = "left"
    RIGHT = "right"
    TOP = "top"
    BOTTOM = "bottom"


# ---------------------------------------------------------------------------
# /api/jobs/precheck
# ---------------------------------------------------------------------------


class PrecheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(..., min_length=1, max_length=128)

    @field_validator("model")
    @classmethod
    def _validate_model(cls, v: str) -> str:
        if not _MODEL_ID_RE.match(v):
            raise ValueError(
                "model must match ^[A-Za-z0-9][A-Za-z0-9._\\-]{0,127}$"
            )
        return v


class PrecheckResponse(BaseModel):
    """Reply for ``POST /api/jobs/precheck`` (design doc §6.3).

    ``site_key`` is included so the frontend can hand it to Turnstile
    without hard-coding the value. ``reason`` is a coarse hint for UX —
    we never reveal which threshold tripped.
    """

    captcha_required: bool
    captcha_provider: str | None = None
    site_key: str | None = None
    reason: str | None = None


# ---------------------------------------------------------------------------
# /api/jobs (create)
# ---------------------------------------------------------------------------


class JobCreatePayload(BaseModel):
    """JSON portion of the multipart ``POST /api/jobs`` body.

    Field set is the union of every parameter every supported model
    accepts (design doc §5.1). Adapter-level validation in
    :class:`app.adapters.openai_v1.OpenAIV1Adapter` /
    :class:`app.adapters.gemini_v1beta.GeminiV1BetaAdapter` is the
    final guard — but the route layer rejects obvious mismatches up
    front against the *effective capabilities* (union over the
    provider pool the user can reach) so a bad request never burns
    queue / quota budget.

    Reference uploads ride alongside this JSON as ``ref_<order>``
    multipart fields; this schema only carries scalars.
    """

    model_config = ConfigDict(extra="forbid")

    # Required.
    model: str = Field(..., min_length=1, max_length=128)
    prompt: str = Field(..., min_length=1, max_length=200_000)

    # Universal.
    n: int = Field(default=1, ge=1, le=64)
    session_id: str | None = None
    client_request_id: str | None = None
    captcha_token: str | None = Field(default=None, min_length=1, max_length=4096)

    # Batch binding (frontend / backend doc v0.3). When the caller is
    # submitting a job as part of a registered batch, it passes the
    # parent ``bat_<10char>`` here; the route handler binds the new
    # ``jobs`` row to it inside the same transaction. ``set_id`` is
    # also accepted top-level so a multi-image slot can reuse a
    # frontend-allocated set across slot.image_count siblings.
    batch_id: str | None = None
    set_id: str | None = None

    # gpt-image-2 fields.
    size: str | None = Field(default=None, max_length=64)
    quality: str | None = Field(default=None, max_length=32)
    output_format: str | None = Field(default=None, max_length=32)
    output_compression: int | None = Field(default=None, ge=0, le=100)
    background: str | None = Field(default=None, max_length=32)
    moderation: str | None = Field(default=None, max_length=32)
    thinking: str | None = Field(default=None, max_length=16)
    stream: bool = False
    partial_images: int = Field(default=0, ge=0, le=16)

    # Gemini fields.
    aspect_ratio: str | None = Field(default=None, max_length=32)
    image_size: str | None = Field(default=None, max_length=16)
    thinking_level: str | None = Field(default=None, max_length=32)
    include_thoughts: bool = False
    google_search: bool = False
    image_search: bool = False

    # ---- Derivation (mask edit / outpaint) — see frontend §3.x -----
    parent_hash_id: str | None = Field(
        default=None,
        max_length=64,
        description="If this submission derives from an existing job, "
        "the parent's hash_id.",
    )
    parent_order: int | None = Field(
        default=None,
        ge=1,
        le=64,
        description="When ``parent_hash_id`` points at a create-set parent "
        "(one job with N images), this is the 1-based image order that "
        "was used as the source. Must be empty when ``parent_hash_id`` is.",
    )
    derivation_kind: DerivationKind | None = None
    outpaint_directions: list[OutpaintDirection] | None = Field(
        default=None,
        description="Directions enabled for canvas extension. Required "
        "when derivation_kind is 'outpaint'.",
    )
    outpaint_amount: str | None = Field(
        default=None,
        max_length=8,
        description="Percent ('25%') or pixel ('256px') extend amount. "
        "Required when derivation_kind is 'outpaint'.",
    )

    @field_validator("parent_hash_id")
    @classmethod
    def _validate_parent_hash_id(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not _HASH_ID_RE.match(v):
            raise ValueError("parent_hash_id has an invalid format")
        return v

    @field_validator("outpaint_amount")
    @classmethod
    def _validate_outpaint_amount(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not _OUTPAINT_AMOUNT_RE.match(v):
            raise ValueError(
                "outpaint_amount must be like '25%' or '256px'"
            )
        return v

    @model_validator(mode="after")
    def _check_derivation(self) -> "JobCreatePayload":
        a = self.parent_hash_id is not None
        b = self.derivation_kind is not None
        if a != b:
            raise ValueError(
                "parent_hash_id and derivation_kind must be both set or both unset"
            )
        if self.parent_order is not None and self.parent_hash_id is None:
            raise ValueError(
                "parent_order requires parent_hash_id"
            )
        is_outpaint = self.derivation_kind == DerivationKind.OUTPAINT
        if is_outpaint:
            if not self.outpaint_directions:
                raise ValueError(
                    "outpaint requires outpaint_directions (at least 1)"
                )
            if not self.outpaint_amount:
                raise ValueError("outpaint requires outpaint_amount")
            # de-duplicate directions
            uniq: list[OutpaintDirection] = []
            for d in self.outpaint_directions:
                if d not in uniq:
                    uniq.append(d)
            object.__setattr__(self, "outpaint_directions", uniq)
        else:
            if self.outpaint_directions or self.outpaint_amount:
                raise ValueError(
                    "outpaint_directions / outpaint_amount only valid "
                    "when derivation_kind=outpaint"
                )
        return self

    @field_validator("model")
    @classmethod
    def _validate_model(cls, v: str) -> str:
        if not _MODEL_ID_RE.match(v):
            raise ValueError(
                "model must match ^[A-Za-z0-9][A-Za-z0-9._\\-]{0,127}$"
            )
        return v

    @field_validator("session_id")
    @classmethod
    def _validate_session_id(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not _SESSION_ID_RE.match(v):
            raise ValueError("session_id must match ^sess_[A-Za-z0-9]{10}$")
        return v

    @field_validator("client_request_id")
    @classmethod
    def _validate_client_request_id(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not _CLIENT_REQUEST_ID_RE.match(v):
            raise ValueError("client_request_id must be 1..64 chars [A-Za-z0-9_-]")
        return v

    @field_validator("batch_id")
    @classmethod
    def _validate_batch_id(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not _BATCH_ID_RE.match(v):
            raise ValueError("batch_id must match ^bat_[A-Za-z0-9]{10}$")
        return v

    @field_validator("set_id")
    @classmethod
    def _validate_set_id_field(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not _SET_ID_RE.match(v):
            raise ValueError("set_id must match ^set_[A-Za-z0-9]{10}$")
        return v

    def to_normalized_dict(self) -> dict[str, Any]:
        """Return the params dict the executor's NormalizedRequest expects.

        Strips the route-only fields (``session_id``, ``client_request_id``,
        ``captcha_token``) — those belong to job metadata, not adapter
        input. ``references`` and ``mask`` are added by the route handler
        once it has read the multipart files.
        """
        out = self.model_dump(exclude_none=True)
        for k in (
            "session_id",
            "client_request_id",
            "captcha_token",
            "parent_hash_id",
            "parent_order",
            "derivation_kind",
            "outpaint_directions",
            "outpaint_amount",
            "batch_id",
            "set_id",
        ):
            out.pop(k, None)
        return out


class JobCreateResponse(BaseModel):
    """Reply for ``POST /api/jobs`` (design doc §6.4)."""

    model_config = ConfigDict(extra="forbid")

    hash_id: str
    seq_no: int
    model: str
    status: str
    position: int | None
    estimated_wait_seconds: int | None
    queued_at: datetime
    set_id: str | None = None
    client_request_id: str | None = None
    parent_hash_id: str | None = None
    parent_order: int | None = None
    derivation_kind: str | None = None
    batch_id: str | None = None


# ---------------------------------------------------------------------------
# /api/jobs/<hash>/cancel and DELETE /api/jobs/<hash>
# ---------------------------------------------------------------------------


class JobActionResponse(BaseModel):
    """Reply for ``cancel`` / ``DELETE`` — both share the shape."""

    model_config = ConfigDict(extra="forbid")

    hash_id: str
    status: str
    ok: bool = True


__all__ = (
    "PrecheckRequest",
    "PrecheckResponse",
    "JobCreatePayload",
    "JobCreateResponse",
    "JobActionResponse",
    "DerivationKind",
    "OutpaintDirection",
)
