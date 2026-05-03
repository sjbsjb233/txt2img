"""Pydantic shapes for the admin provider endpoints.

Lives next to ``schemas/admin_config.py`` rather than under the route
module so a frontend type generator can emit one ``adminProvider.ts``
mirroring this file. Field names mirror the design doc §4.1 / §4.2 / §13.4.

Three top-level groups:

1. **Capability schema** (``ProviderModelCapabilities``) — the per-(provider,
   model) parameter whitelist surfaced via ``/api/models``. Stored as
   JSON in ``provider_models.capabilities_json``; we still validate
   shape here so the column never holds garbage. Per design doc §4.3.
2. **CRUD bodies** (``ProviderCreate`` / ``ProviderPatch`` / etc.) — what
   admin sends.
3. **Response views** (``ProviderResponse`` / ``ProviderListItem``) — what
   admin reads back. ``api_key`` is *always* masked; the cleartext only
   exists during the originating create/patch.
"""

from __future__ import annotations

import re
from typing import Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator


# Provider ids are user-supplied; restrict to a sane character set so
# they're safe to interpolate into URLs and log lines.
_PROVIDER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# Tier names that ``provider_tier_access`` accepts.
_VALID_TIERS = ("vip", "premium", "standard", "free")


# ---------------------------------------------------------------------------
# Capability schema
# ---------------------------------------------------------------------------


class ProviderModelCapabilities(BaseModel):
    """Per-(provider, model) parameter whitelist.

    Every field is optional. The semantics matches design doc §4.3:

    - List fields ``[]`` → user cannot set the parameter at all (frontend
      hides the control). ``None`` (omitted) → no opinion at this layer;
      fall through to the model's intrinsic full set.
    - Numeric fields (``n_max`` / ``partial_images_max`` /
      ``max_reference_images`` / ``max_prompt_chars``) are upper bounds.
    - Boolean fields (``include_thoughts`` / ``google_search`` /
      ``image_search`` / ``stream`` / ``supports_transparent_bg`` /
      ``supports_mask``) say whether the user can opt in.

    We do NOT enforce that, e.g., ``n_max`` matches the underlying
    model's true upper bound — admins might intentionally cap a relay
    that has poor concurrency. The ``/api/models`` endpoint (PR-13) is
    responsible for taking the union over enabled providers.
    """

    model_config = ConfigDict(extra="forbid")

    # Discrete value sets
    size: list[str] | None = None
    aspect_ratio: list[str] | None = None
    image_size: list[str] | None = None
    quality: list[str] | None = None
    output_format: list[str] | None = None
    background: list[str] | None = None
    moderation: list[str] | None = None
    thinking_level: list[str] | None = None

    # Numeric / boolean knobs
    n_max: int | None = Field(default=None, ge=1, le=64)
    partial_images_max: int | None = Field(default=None, ge=0, le=16)
    max_reference_images: int | None = Field(default=None, ge=0, le=64)
    max_prompt_chars: int | None = Field(default=None, ge=1, le=200_000)
    include_thoughts: bool | None = None
    google_search: bool | None = None
    image_search: bool | None = None
    stream: bool | None = None
    supports_transparent_bg: bool | None = None
    supports_mask: bool | None = None

    # Free-form text — for admin notes that don't deserve a proper field.
    extra_notes: str | None = Field(default=None, max_length=500)


class ProviderModelEntry(BaseModel):
    """One ``(model, capabilities)`` pair on a provider."""

    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(..., min_length=1, max_length=128)
    capabilities: ProviderModelCapabilities = Field(
        default_factory=ProviderModelCapabilities
    )
    enabled: bool = True


# ---------------------------------------------------------------------------
# Adapter self-describing capability schema (PR-A)
# ---------------------------------------------------------------------------
#
# Each adapter declares which capability fields it understands so the
# admin UI can render a form tailored to that adapter, instead of the
# union of every possible capability. The `k` field on each entry MUST
# be a field name on ``ProviderModelCapabilities`` above — admin still
# stores results into the same ``capabilities_json`` blob, validated by
# that pydantic class.


class CapabilityFieldList(BaseModel):
    """Discrete-value capability field rendered as a chip multi-select."""

    model_config = ConfigDict(extra="forbid")

    k: str
    kind: Literal["list"] = "list"
    options: list[str]
    label: str | None = None
    help: str | None = None


class CapabilityFieldInt(BaseModel):
    """Numeric upper-bound capability field rendered as a number input."""

    model_config = ConfigDict(extra="forbid")

    k: str
    kind: Literal["int"] = "int"
    min: int | None = None
    max: int | None = None
    label: str | None = None
    help: str | None = None


class CapabilityFieldBool(BaseModel):
    """Tri-state boolean capability field (unset / true / false)."""

    model_config = ConfigDict(extra="forbid")

    k: str
    kind: Literal["bool"] = "bool"
    label: str | None = None
    help: str | None = None


CapabilityField = Union[CapabilityFieldList, CapabilityFieldInt, CapabilityFieldBool]


# ---------------------------------------------------------------------------
# Create / patch / sub-routes
# ---------------------------------------------------------------------------


class ProviderCreate(BaseModel):
    """Body for ``POST /api/admin/providers`` (design doc §4.2)."""

    model_config = ConfigDict(extra="forbid")

    provider_id: str
    label: str = Field(..., min_length=1, max_length=200)
    adapter_type: str = Field(..., min_length=1, max_length=64)
    base_url: str = Field(..., min_length=1, max_length=2000)
    api_key: str = Field(..., min_length=1, max_length=4096)
    cost_per_image_cny: float = Field(..., ge=0)
    initial_balance_cny: float = Field(..., ge=0)
    enabled: bool = True
    note: str | None = Field(default=None, max_length=2000)
    max_concurrency: int = Field(default=10, ge=1, le=10_000)
    rpm_limit: int = Field(default=600, ge=1, le=1_000_000)
    supported_models: list[ProviderModelEntry] = Field(default_factory=list)
    tier_access: list[str] = Field(default_factory=list)

    @field_validator("provider_id")
    @classmethod
    def _validate_provider_id(cls, v: str) -> str:
        if not _PROVIDER_ID_RE.match(v):
            raise ValueError(
                "provider_id must match [a-z0-9][a-z0-9_-]{0,63}"
            )
        return v

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, v: str) -> str:
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("base_url must start with http:// or https://")
        return v

    @field_validator("tier_access")
    @classmethod
    def _validate_tier_access(cls, v: list[str]) -> list[str]:
        for t in v:
            if t not in _VALID_TIERS:
                raise ValueError(
                    f"invalid tier in tier_access: {t!r}; "
                    f"must be one of {_VALID_TIERS}"
                )
        # De-duplicate while preserving caller order.
        seen: set[str] = set()
        unique: list[str] = []
        for t in v:
            if t not in seen:
                seen.add(t)
                unique.append(t)
        return unique


class ProviderPatch(BaseModel):
    """Body for ``PATCH /api/admin/providers/<id>``.

    Every field optional; ``model_fields_set`` distinguishes "omitted"
    from "explicit None" in the handler. ``api_key`` here means *replace
    the stored value*; we never round-trip the ciphertext through the API.
    """

    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, min_length=1, max_length=200)
    base_url: str | None = Field(default=None, min_length=1, max_length=2000)
    api_key: str | None = Field(default=None, min_length=1, max_length=4096)
    cost_per_image_cny: float | None = Field(default=None, ge=0)
    enabled: bool | None = None
    note: str | None = Field(default=None, max_length=2000)
    max_concurrency: int | None = Field(default=None, ge=1, le=10_000)
    rpm_limit: int | None = Field(default=None, ge=1, le=1_000_000)

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("base_url must start with http:// or https://")
        return v


class ProviderModelPatch(BaseModel):
    """Body for ``PATCH /api/admin/providers/<id>/models/<model_id>``."""

    model_config = ConfigDict(extra="forbid")

    capabilities: ProviderModelCapabilities | None = None
    enabled: bool | None = None


class ProviderTierAccessUpdate(BaseModel):
    """Body for ``PATCH /api/admin/providers/<id>/tier-access``.

    Replaces the whole list — partial union/diff is more confusing than
    the bandwidth saving is worth at four tier values.
    """

    model_config = ConfigDict(extra="forbid")

    tiers: list[str] = Field(default_factory=list)

    @field_validator("tiers")
    @classmethod
    def _validate(cls, v: list[str]) -> list[str]:
        for t in v:
            if t not in _VALID_TIERS:
                raise ValueError(
                    f"invalid tier: {t!r}; must be one of {_VALID_TIERS}"
                )
        seen: set[str] = set()
        out: list[str] = []
        for t in v:
            if t not in seen:
                seen.add(t)
                out.append(t)
        return out


class ProviderTopup(BaseModel):
    """Body for ``POST /api/admin/providers/<id>/topup``."""

    model_config = ConfigDict(extra="forbid")

    amount_cny: float = Field(..., gt=0, le=1_000_000)


# ---------------------------------------------------------------------------
# Response views
# ---------------------------------------------------------------------------


class ProviderModelView(BaseModel):
    """Provider-model row as returned to admin."""

    model_id: str
    enabled: bool
    capabilities: dict[str, Any]


class ProviderResponse(BaseModel):
    """Full provider view; shared between list and single-item routes.

    Field naming follows design doc §13.4 literally so a frontend admin
    table can reuse the keys verbatim. Note ``api_key_masked`` (not
    ``api_key``) — the cleartext key never appears in any response.
    """

    id: str
    label: str
    adapter_type: str
    base_url: str
    api_key_masked: str
    cost_per_image_cny: float
    balance_cny: float
    initial_balance_cny: float
    enabled: bool
    note: str | None
    max_concurrency: int
    rpm_limit: int
    circuit_state: str
    cooldown_until: str | None
    supported_models: list[ProviderModelView]
    tier_access: list[str]


class ProviderTopupResponse(BaseModel):
    provider_id: str
    amount_cny: float
    balance_after: float
    promoted_from_drained: bool


class TierAccessResponse(BaseModel):
    provider_id: str
    tiers: list[str]


class ProviderModelUpdateResponse(BaseModel):
    provider_id: str
    model_id: str
    enabled: bool
    capabilities: dict[str, Any]


# ---------------------------------------------------------------------------
# PR-16 additions: live metrics, test ping, reset-circuit
# ---------------------------------------------------------------------------


class ProviderMetricsView(BaseModel):
    """Per-(provider, model) rolling-window summary surfaced to admin.

    All fields are over the same ``provider_scoring.metric_window_seconds``
    window the selector reads, so the admin UI shows the numbers the
    scheduler is actually scoring against.
    """

    model_id: str
    calls: int
    success_rate: float
    p50_ms: float | None
    p95_ms: float | None


class ProviderListItem(ProviderResponse):
    """Provider list row.

    Extends :class:`ProviderResponse` with live runtime telemetry that
    only makes sense on the list view. Single-item GET still returns the
    plain :class:`ProviderResponse` so the wire format there stays
    backward-compatible with PR-06 callers.
    """

    current_concurrency: int
    recent_calls_60s: int
    metrics: list[ProviderMetricsView] = Field(default_factory=list)


class ProviderResetCircuitResponse(BaseModel):
    provider_id: str
    circuit_state: str
    cooldown_until: str | None = None


class ProviderTestRequest(BaseModel):
    """Body for ``POST /api/admin/providers/<id>/test``.

    Both fields optional: with no body we use the first model attached
    to the provider and a short generic prompt. The test does NOT touch
    the ledger or metrics — it's a side-effect-free probe.
    """

    model_config = ConfigDict(extra="forbid")

    model_id: str | None = Field(default=None, min_length=1, max_length=128)
    prompt: str | None = Field(default=None, min_length=1, max_length=4000)


class ProviderTestResponse(BaseModel):
    provider_id: str
    model_id: str
    ok: bool
    latency_ms: float
    image_count: int = 0
    error_kind: str | None = None
    error_message: str | None = None
