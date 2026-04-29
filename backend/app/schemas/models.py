"""Schemas for ``GET /api/models`` (PR-13, design doc §6.2).

The response shape is what the Create page renders against. Fields mirror
design doc §6.2 verbatim so the frontend type generator can emit a
matching ``Models.ts`` from this module.

Two pieces of data flow through here:

- **Capabilities** — the union of per-(provider, model) capability JSON
  across every provider that is *enabled, has the model enabled, and is
  open to the user's tier*. List values union, numeric upper bounds take
  the max, booleans turn on if any provider opts in. The frontend never
  sees the per-provider rows; it only sees the merged user-visible
  whitelist.
- **Defaults** — model-intrinsic recommended starting values per design
  doc §5.3. Picked from a hard-coded table because they are user-visible
  copy / UX choices, not capability data.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class ModelCapabilities(BaseModel):
    """User-visible capability whitelist for one model.

    Field semantics mirror :class:`app.schemas.provider.ProviderModelCapabilities`
    but flattened to "what the frontend should render". Lists are the
    union of per-provider opinions; numerics are the maximum opt-in value
    seen on any provider; booleans are True iff at least one provider
    permits the feature.

    A field that is missing in *every* provider for this (user, model)
    pair is left as ``None`` here. Frontend renders nothing for ``None``.
    """

    model_config = ConfigDict(extra="forbid")

    # Discrete choice fields — frontend renders chip groups.
    size: list[str] | None = None
    aspect_ratio: list[str] | None = None
    image_size: list[str] | None = None
    quality: list[str] | None = None
    output_format: list[str] | None = None
    background: list[str] | None = None
    moderation: list[str] | None = None
    thinking_level: list[str] | None = None

    # Numeric upper bounds.
    n_max: int | None = None
    partial_images_max: int | None = None
    max_reference_images: int | None = None
    max_prompt_chars: int | None = None

    # Boolean opt-ins.
    include_thoughts: bool | None = None
    google_search: bool | None = None
    image_search: bool | None = None
    stream: bool | None = None
    supports_transparent_bg: bool | None = None
    supports_mask: bool | None = None


class ModelDefaults(BaseModel):
    """Model-intrinsic recommended starting values (design doc §5.3).

    Used by the Create page to populate the parameter panel when the
    user picks a model. Defaults are advisory: the frontend can keep
    state across model switches if the user already chose a value that
    is still in ``capabilities``.
    """

    model_config = ConfigDict(extra="allow")

    n: int | None = None
    size: str | None = None
    quality: str | None = None
    output_format: str | None = None
    background: str | None = None
    moderation: str | None = None
    aspect_ratio: str | None = None
    image_size: str | None = None
    thinking_level: str | None = None
    include_thoughts: bool | None = None
    google_search: bool | None = None
    image_search: bool | None = None


class ModelDescriptor(BaseModel):
    """One entry in the ``/api/models`` ``models`` list."""

    model_config = ConfigDict(extra="forbid")

    model_id: str
    display_name: str
    tag: str | None = None
    logo: str | None = None
    blurb: str | None = None
    available: bool
    available_reason: str | None = None
    capabilities: ModelCapabilities
    defaults: ModelDefaults


class ModelSessionEntry(BaseModel):
    """Lightweight session entry for the Create-page session picker."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    image_count: int
    updated_at: str


class ModelsResponse(BaseModel):
    """Top-level ``GET /api/models`` shape."""

    model_config = ConfigDict(extra="forbid")

    models: list[ModelDescriptor]
    sessions: list[ModelSessionEntry]


__all__: tuple[str, ...] = (
    "ModelCapabilities",
    "ModelDefaults",
    "ModelDescriptor",
    "ModelSessionEntry",
    "ModelsResponse",
)


# Re-export ``Any`` so ``__all__`` static checks don't strip the shim.
_ = Any
