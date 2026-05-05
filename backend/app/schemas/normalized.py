"""Adapter-facing request / response shapes (design doc §5.2).

The "normalized" types are the contract between the job executor (caller)
and adapters (implementations). Frontend always submits the normalized
shape; each adapter is responsible for translating it into the upstream
wire format.

Three goals of keeping a normalized layer:

1. The executor doesn't know or care which provider it ends up on — it
   passes the same ``NormalizedRequest`` to whichever adapter wins
   selection, and falls back across adapters without translation.
2. Adapters can be added without touching the executor: register a new
   subclass, add its ``adapter_type`` to a provider, and the executor
   keeps working.
3. Errors have a single standard taxonomy regardless of upstream wire
   format, so the circuit breaker / metrics engine can consume them
   uniformly.

This module is import-time pure: no httpx, no DB, no I/O. It is safe to
import from any layer.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Provider config (passed to ``BaseAdapter.generate``)
# ---------------------------------------------------------------------------


class ProviderConfig(BaseModel):
    """The minimum a provider needs to be callable.

    The full ``providers`` row carries more metadata (balance, RPM, circuit
    state, capabilities) but adapters intentionally see only the fields
    they need to wire a request. The executor decrypts ``api_key`` before
    constructing this — adapters never touch the encrypted blob.
    """

    id: str
    base_url: str
    api_key: str
    adapter_type: str
    # Per-call timeout in seconds; the executor may shorten this for
    # provider probes. Default is generous because image generation can
    # take 30-60s on cold paths.
    timeout_seconds: float = Field(default=600.0, gt=0)


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------


class NormalizedReference(BaseModel):
    """A single reference image for editing / multi-image fusion.

    ``order`` is 1-indexed and load-bearing: adapters MUST emit references
    to the upstream in ascending ``order`` (design doc §5.2 / §6.5). This
    matters because Gemini's parts array is positional and OpenAI's edits
    endpoint treats the first image specially when a mask is present.
    """

    order: int = Field(..., ge=1)
    mime: str
    # Base64 (no data: prefix). Adapters decode just before sending.
    data_b64: str
    # Filename is informational; surfaces on multipart uploads.
    filename: str | None = None


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class NormalizedRequest(BaseModel):
    """The frontend's submission, pre-mapping.

    Field naming follows design doc §5.1: every parameter exposed in
    ``/api/models`` capabilities maps to a field here. Fields not relevant
    to the chosen model stay at their defaults and the adapter ignores
    them. The adapter is also responsible for raising INVALID_PARAMETER
    when the caller sets a value the upstream cannot accept (e.g.
    ``background='transparent'`` on gpt-image-2).
    """

    model: str
    prompt: str
    n: int = Field(default=1, ge=1, le=10)

    # ----- gpt-image-2 -------------------------------------------------
    size: str | None = None
    quality: str | None = None
    output_format: str | None = None
    output_compression: int | None = Field(default=None, ge=0, le=100)
    background: str | None = None
    moderation: str | None = None
    stream: bool = False
    partial_images: int = Field(default=0, ge=0, le=3)
    user: str | None = None

    # ----- gemini ------------------------------------------------------
    aspect_ratio: str | None = None
    image_size: str | None = None
    thinking_level: str | None = None
    include_thoughts: bool = False
    google_search: bool = False
    image_search: bool = False

    # ----- attached images --------------------------------------------
    references: list[NormalizedReference] = Field(default_factory=list)
    # Edits endpoint only (gpt-image-2). Single PNG with alpha.
    mask: NormalizedReference | None = None


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------


class NormalizedImage(BaseModel):
    """A single image returned by an upstream call.

    ``data`` carries the raw bytes (already base64-decoded); the executor
    stores it via ``services.image_io``. Width/height are optional —
    OpenAI does not declare them in the response and we'd rather defer
    measurement to the storage layer than block on Pillow inside the
    adapter.
    """

    data: bytes
    mime: str
    width: int | None = None
    height: int | None = None
    # Optional per-image metadata (revised prompt, thought_signature, etc.)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"arbitrary_types_allowed": True}


class NormalizedResponse(BaseModel):
    """Adapter return type.

    Always carries ``images`` (possibly empty, e.g. when an upstream
    answers TEXT-only and we count it as a soft failure). ``raw`` keeps
    a redacted upstream snapshot so the executor can persist
    ``data/jobs/<hash>/upstream/attempt_N.json`` for debugging.
    """

    images: list[NormalizedImage] = Field(default_factory=list)
    image_count: int = 0
    text: str | None = None
    # Free-form metadata: groundingMetadata, search queries, latency hints.
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Truncated upstream payload, suitable for ``json.dumps`` to disk.
    raw: dict[str, Any] | None = None

    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# Standardised error
# ---------------------------------------------------------------------------


class StandardErrorKind(str, Enum):
    """Coarse-grained error taxonomy for the circuit breaker / metrics.

    Anything an adapter wants to surface to the executor must reduce to
    one of these. The mapping from upstream wire codes is documented per
    adapter in ``normalize_error``.
    """

    AUTH = "AUTH"  # 401 / 403 from upstream
    RATE_LIMITED = "RATE_LIMITED"  # 429
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"  # network/read timeout
    UPSTREAM_ERROR = "UPSTREAM_ERROR"  # 5xx
    INVALID_PARAMETER = "INVALID_PARAMETER"  # 400 / our pre-flight rejects
    UNSUPPORTED_MODEL = "UNSUPPORTED_MODEL"
    NETWORK_ERROR = "NETWORK_ERROR"  # connection refused / DNS / TLS
    EMPTY_RESPONSE = "EMPTY_RESPONSE"  # upstream returned no image at all
    OTHER = "OTHER"


class StandardError(Exception):
    """Adapter-side exception with a structured taxonomy.

    The circuit breaker treats any ``StandardError`` as a failure; the
    metrics engine records ``kind`` so admins can see e.g. "this provider
    is rate-limited 12% of the time". ``upstream_status`` and
    ``upstream_body_excerpt`` are kept for the per-attempt debug log; they
    are never returned to end users.
    """

    def __init__(
        self,
        kind: StandardErrorKind,
        message: str,
        *,
        field: str | None = None,
        upstream_status: int | None = None,
        upstream_body_excerpt: str | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.field = field
        self.upstream_status = upstream_status
        self.upstream_body_excerpt = upstream_body_excerpt

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "message": self.message,
            "field": self.field,
            "upstream_status": self.upstream_status,
            "upstream_body_excerpt": self.upstream_body_excerpt,
        }
