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
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


# Models the caller can request. Adapter / provider validation is the
# authoritative gate; this regex only constrains the URL/log-friendly
# shape so a malformed value doesn't propagate further.
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,127}$")
_SESSION_ID_RE = re.compile(r"^sess_[A-Za-z0-9]{10}$")
_CLIENT_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


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

    def to_normalized_dict(self) -> dict[str, Any]:
        """Return the params dict the executor's NormalizedRequest expects.

        Strips the route-only fields (``session_id``, ``client_request_id``,
        ``captcha_token``) — those belong to job metadata, not adapter
        input. ``references`` and ``mask`` are added by the route handler
        once it has read the multipart files.
        """
        out = self.model_dump(exclude_none=True)
        for k in ("session_id", "client_request_id", "captcha_token"):
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
)
