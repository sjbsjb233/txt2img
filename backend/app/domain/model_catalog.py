"""Model catalog — what the user can ask for, given their tier.

This domain module owns the logic behind ``GET /api/models`` (PR-13,
design doc §6.2). Two responsibilities:

1. **Tier-filtered model discovery.** Walk every enabled
   ``provider_models`` row, intersect with the user's tier access,
   and surface the union of available models. A model is "available"
   when at least one passing provider has a healthy circuit.
2. **Capability union.** Per design doc §6.2 the frontend renders the
   *union* of capability fields across the providers the user could
   reach. List fields union, numeric upper bounds take the max,
   booleans turn on if any provider opts in. Fields that no provider
   has an opinion on (i.e. ``None`` everywhere) collapse to ``None``
   so the frontend hides their controls.

The module is read-only against the DB and depends on
:class:`AdapterRegistry` only for the model display copy. It does not
take an upstream call or touch the metrics engine — admission is
orthogonal to display.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import AdapterRegistry, get_registry
from app.db.engine import get_session
from app.db.models import (
    Provider,
    ProviderModel,
    ProviderModelTierAccess,
    ProviderTierAccess,
    User,
)
from app.schemas.models import (
    ModelCapabilities,
    ModelDefaults,
    ModelDescriptor,
    ModelUIField,
)

logger = logging.getLogger("txt2img.model_catalog")


# ---------------------------------------------------------------------------
# Display metadata for the three v1 models (design doc §5.1 / §5.3 / §6.2).
#
# Hard-coded because the strings are user-facing copy that lives outside the
# admin / provider layer. Adding a new model means appending here once the
# adapter exposes it. Anything not in this map still surfaces (with a
# generic display name) so an experimental relay model isn't invisible.
# ---------------------------------------------------------------------------
# ``primary_adapter`` is the single source of truth for the model's
# Create-page ``ui_schema`` (design v2 §3.2). The runtime allows multiple
# adapters to claim the same ``model_id`` (legitimate but uncommon — e.g.
# a private-protocol relay also speaking gpt-image-2), so without an
# explicit pick the panel layout would be non-deterministic. Selection /
# scheduling stay independent of this field.
_MODEL_DISPLAY: dict[str, dict[str, str | None]] = {
    "gpt-image-2": {
        "display_name": "ChatGPT Images 2.0",
        "tag": "RECOMMENDED",
        "logo": "chatgpt",
        "blurb": (
            "Best for editorial, photoreal, brand. Strong text & "
            "composition control."
        ),
        "primary_adapter": "openai_v1",
    },
    "gpt-image-2-2026-04-21": {
        "display_name": "ChatGPT Images 2.0 (snapshot)",
        "tag": "RECOMMENDED",
        "logo": "chatgpt",
        "blurb": "Pinned 2026-04-21 snapshot of gpt-image-2.",
        "primary_adapter": "openai_v1",
    },
    "gemini-3-pro-image-preview": {
        "display_name": "Gemini 3 Pro (Nano Banana Pro)",
        "tag": "QUALITY",
        "logo": "flash",
        "blurb": (
            "High-fidelity gemini preview with built-in thinking. "
            "Up to 4K, 14 reference images."
        ),
        "primary_adapter": "gemini_v1beta",
    },
    "gemini-3.1-flash-image-preview": {
        "display_name": "Gemini 3.1 Flash (Nano Banana 2)",
        "tag": "FAST",
        "logo": "flash",
        "blurb": (
            "Fast iteration with configurable thinking, image search, "
            "and extreme aspect ratios."
        ),
        "primary_adapter": "gemini_v1beta",
    },
    "gemini-2.5-flash-image": {
        "display_name": "Gemini 2.5 Flash Image",
        "tag": "LEGACY",
        "logo": "flash",
        "blurb": "Legacy Gemini relay model.",
        "primary_adapter": "gemini_v1beta",
    },
}


# Recommended starting values (design doc §5.3). Same hard-coded rationale
# as ``_MODEL_DISPLAY`` — these are UX defaults, not capability data.
_MODEL_DEFAULTS: dict[str, ModelDefaults] = {
    "gpt-image-2": ModelDefaults(
        n=4,
        size="auto",
        quality="auto",
        output_format="png",
        background="auto",
        moderation="auto",
    ),
    "gpt-image-2-2026-04-21": ModelDefaults(
        n=4,
        size="auto",
        quality="auto",
        output_format="png",
        background="auto",
        moderation="auto",
    ),
    "gemini-3-pro-image-preview": ModelDefaults(
        n=1,
        aspect_ratio="1:1",
        image_size="1K",
        google_search=False,
    ),
    "gemini-3.1-flash-image-preview": ModelDefaults(
        n=1,
        aspect_ratio="1:1",
        image_size="1K",
        thinking_level="minimal",
        include_thoughts=False,
        google_search=False,
        image_search=False,
    ),
    "gemini-2.5-flash-image": ModelDefaults(
        n=1,
        aspect_ratio="1:1",
        image_size="1K",
    ),
}


# Reasons surfaced when a model is rendered grey. Value is the design-doc
# §6.2 wording verbatim so frontend i18n only ever has to map the code.
NO_PROVIDER_FOR_TIER = "no_provider_for_tier"
NO_CAPABLE_PROVIDER = "no_capable_provider"
ALL_PROVIDERS_CIRCUIT_OPEN = "all_providers_circuit_open"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ProviderRow:
    """Local snapshot of one provider+model row, stripped of identity.

    The catalog code never returns these to callers — they're an
    intermediate so capability merging operates on plain dicts.
    """

    provider_id: str
    enabled: bool
    circuit_state: str
    capabilities: dict[str, Any]


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


async def list_models_for_user(user: User) -> list[ModelDescriptor]:
    """Return every model the user can see, with merged capabilities.

    Goes to the DB once for the joined provider / model / tier-access
    rows, then groups in Python. Result order is deterministic
    (alphabetical by ``model_id``) so the frontend can rely on it for
    skeleton rendering.
    """
    rows_by_model = await _load_model_rows_for_tier(user.tier)

    # Always include the model display table so a model with no providers
    # configured is still listed (greyed out). Without this, a fresh
    # deployment with zero providers would surface an empty list and the
    # frontend would have nothing to render.
    all_models: set[str] = set(rows_by_model.keys())
    all_models.update(_MODEL_DISPLAY.keys())

    descriptors: list[ModelDescriptor] = []
    for model_id in sorted(all_models):
        rows = rows_by_model.get(model_id, [])
        descriptor = _build_descriptor(model_id, rows)
        descriptors.append(descriptor)
    return descriptors


async def effective_capabilities_for_user(
    user: User, model: str
) -> ModelCapabilities:
    """Return the capability union for one user-model pair.

    The caller (``POST /api/jobs``) uses this to validate the request
    against the very same surface the frontend rendered. The DB query
    is the same one ``list_models_for_user`` runs — we re-issue it so
    job-create stays single-call without a global cache that could go
    stale during admin edits.
    """
    rows_by_model = await _load_model_rows_for_tier(user.tier)
    rows = rows_by_model.get(model, [])
    return _merge_capabilities([row.capabilities for row in rows if row.enabled])


# ---------------------------------------------------------------------------
# DB load
# ---------------------------------------------------------------------------


async def _load_model_rows_for_tier(
    tier: str,
) -> dict[str, list[_ProviderRow]]:
    """Group enabled provider+model rows the user can reach by ``model_id``.

    Steps:

    1. Pull every (provider, model) pair where both rows are enabled
       and the provider's adapter is registered.
    2. Pull tier-access (provider-level) and per-(provider, model)
       overrides in two bulk queries.
    3. For each pair, decide whether the user's tier is on the
       whitelist and emit a :class:`_ProviderRow` snapshot.

    "User's tier is whitelisted" is computed exactly like
    :class:`app.domain.provider_selector.ProviderSelector._hard_filter`
    so admission and display agree about who can use what.
    """
    registry = get_registry()

    async with get_session() as session:
        rows = (
            await session.execute(
                select(Provider, ProviderModel)
                .join(ProviderModel, ProviderModel.provider_id == Provider.id)
                .where(
                    Provider.enabled == 1,
                    ProviderModel.enabled == 1,
                )
            )
        ).all()

        if not rows:
            return {}

        provider_ids = {p.id for (p, _m) in rows}

        tier_rows = (
            await session.execute(
                select(
                    ProviderTierAccess.provider_id,
                    ProviderTierAccess.tier,
                ).where(ProviderTierAccess.provider_id.in_(provider_ids))
            )
        ).all()
        pmta_rows = (
            await session.execute(
                select(
                    ProviderModelTierAccess.provider_id,
                    ProviderModelTierAccess.model_id,
                    ProviderModelTierAccess.tier,
                ).where(
                    ProviderModelTierAccess.provider_id.in_(provider_ids)
                )
            )
        ).all()

    provider_tiers: dict[str, set[str]] = {}
    for pid, t in tier_rows:
        provider_tiers.setdefault(pid, set()).add(t)
    pmta_lookup: dict[tuple[str, str], set[str]] = {}
    for pid, mid, t in pmta_rows:
        pmta_lookup.setdefault((pid, mid), set()).add(t)

    grouped: dict[str, list[_ProviderRow]] = {}
    for provider, model_row in rows:
        # PMTA overrides provider-level tier-access when present for this
        # specific (provider, model).
        allowed = pmta_lookup.get(
            (provider.id, model_row.model_id),
            provider_tiers.get(provider.id, set()),
        )
        if tier not in allowed:
            continue
        # Skip rows whose adapter isn't registered. A capability surface
        # we can't actually call is misleading.
        if not registry.has(provider.adapter_type):
            continue
        snapshot = _ProviderRow(
            provider_id=provider.id,
            enabled=True,
            circuit_state=provider.circuit_state or "healthy",
            capabilities=_safe_load_caps(model_row.capabilities_json),
        )
        grouped.setdefault(model_row.model_id, []).append(snapshot)
    return grouped


# ---------------------------------------------------------------------------
# Display assembly
# ---------------------------------------------------------------------------


def _build_descriptor(
    model_id: str,
    rows: Sequence[_ProviderRow],
) -> ModelDescriptor:
    display = _MODEL_DISPLAY.get(model_id, {})
    defaults = _MODEL_DEFAULTS.get(model_id, ModelDefaults())
    ui_schema = _resolve_ui_schema(model_id)

    if not rows:
        # No provider has this model on for the user's tier. Could be
        # "no provider configured at all" or "all configured ones are
        # closed to this tier". We can't tell from this layer alone, so
        # we surface the broader reason — the admin debug view shows
        # detail.
        reason = NO_PROVIDER_FOR_TIER
        return ModelDescriptor(
            model_id=model_id,
            display_name=str(
                display.get("display_name") or model_id
            ),
            tag=display.get("tag"),
            logo=display.get("logo"),
            blurb=display.get("blurb"),
            available=False,
            available_reason=reason,
            capabilities=ModelCapabilities(),
            defaults=defaults,
            ui_schema=ui_schema,
        )

    healthy_rows = [r for r in rows if r.circuit_state == "healthy"]
    available = bool(healthy_rows)
    available_reason = (
        None
        if available
        else ALL_PROVIDERS_CIRCUIT_OPEN
    )

    # Capabilities merge across *all* rows the user could reach, even
    # currently unhealthy ones — so the frontend's parameter set doesn't
    # whiplash when a single provider's circuit flips. Selection-time
    # (not display-time) is where unhealthy providers get filtered.
    merged = _merge_capabilities([r.capabilities for r in rows])

    return ModelDescriptor(
        model_id=model_id,
        display_name=str(display.get("display_name") or model_id),
        tag=display.get("tag"),
        logo=display.get("logo"),
        blurb=display.get("blurb"),
        available=available,
        available_reason=available_reason,
        capabilities=merged,
        defaults=defaults,
        ui_schema=ui_schema,
    )


# ---------------------------------------------------------------------------
# UI schema resolution (design v2 §3.2 / §4.6)
# ---------------------------------------------------------------------------


def _resolve_ui_schema(model_id: str) -> list[ModelUIField]:
    """Look up the Create-page panel layout for ``model_id``.

    Always goes through ``_MODEL_DISPLAY[model_id]['primary_adapter']``
    so the panel source is deterministic even when multiple adapters
    declare the same model. Failure modes (model absent from the
    display table, primary adapter unregistered, primary adapter
    refuses the model) all log a warning and degrade to ``[]`` — the
    frontend shows a minimal panel rather than crashing.
    """
    display = _MODEL_DISPLAY.get(model_id, {})
    primary_type = display.get("primary_adapter")
    if not primary_type:
        # Untracked model id (admin enabled an experimental relay that
        # we don't have a display row for). Return empty so the panel
        # doesn't disappear; defaults still ship.
        return []

    registry = get_registry()
    if not registry.has(primary_type):
        logger.warning(
            "model %r declares primary_adapter %r but it is not registered; "
            "ui_schema will be empty",
            model_id,
            primary_type,
        )
        return []

    adapter = registry.get(primary_type)
    try:
        return list(adapter.ui_schema(model_id))
    except ValueError:
        logger.warning(
            "primary_adapter %r refused to produce ui_schema for model %r; "
            "check _MODEL_DISPLAY for stale primary_adapter mapping",
            primary_type,
            model_id,
        )
        return []
    except Exception as exc:  # pragma: no cover — protective
        logger.exception(
            "ui_schema for model %r raised in adapter %r: %s",
            model_id,
            primary_type,
            exc,
        )
        return []


def validate_primary_adapters() -> None:
    """Sanity-check ``_MODEL_DISPLAY.primary_adapter`` wiring at startup.

    Logs warnings only — a deployment may legitimately disable an
    adapter for ops reasons, and we don't want to block process start
    over a Create-page panel issue. Operators read these warnings to
    spot stale or typo'd ``primary_adapter`` values.
    """
    registry = get_registry()
    for model_id, display in _MODEL_DISPLAY.items():
        primary_type = display.get("primary_adapter")

        if not primary_type:
            logger.warning(
                "model_catalog: %r in _MODEL_DISPLAY has no primary_adapter; "
                "Create page will show empty parameter panel for this model",
                model_id,
            )
            continue

        if not registry.has(primary_type):
            logger.warning(
                "model_catalog: %r declares primary_adapter %r but adapter "
                "is not registered (admin disabled? typo?)",
                model_id,
                primary_type,
            )
            continue

        adapter = registry.get(primary_type)
        if model_id not in adapter.supported_models():
            logger.warning(
                "model_catalog: %r declares primary_adapter %r but the "
                "adapter does not list it in supported_models() — "
                "primary_adapter may be stale",
                model_id,
                primary_type,
            )


# ---------------------------------------------------------------------------
# Capability merge — the meat of the public contract
# ---------------------------------------------------------------------------


# Discrete-choice fields → list[str] union.
_LIST_KEYS: tuple[str, ...] = (
    "size",
    "aspect_ratio",
    "image_size",
    "quality",
    "output_format",
    "background",
    "moderation",
    "thinking_level",
)

# Numeric upper bounds → max across providers.
_NUMERIC_KEYS: tuple[str, ...] = (
    "n_max",
    "partial_images_max",
    "max_reference_images",
    "max_prompt_chars",
)

# Boolean opt-ins → True iff any provider says True.
_BOOL_KEYS: tuple[str, ...] = (
    "include_thoughts",
    "google_search",
    "image_search",
    "stream",
    "supports_transparent_bg",
    "supports_mask",
)


def _merge_capabilities(
    sources: Iterable[Mapping[str, Any]],
) -> ModelCapabilities:
    """Combine per-provider capability dicts into one user-visible view.

    Rules (design doc §6.2):

    - Lists union; if a key is *missing* in one provider's row but
      present in another, the present provider wins. A provider that
      explicitly sets ``[]`` is opting out of the field, but as long
      as at least one provider opts in we still surface the field.
    - Numerics take the max ceiling.
    - Booleans OR together.

    Returning ``ModelCapabilities()`` on empty input is intentional —
    the caller shows a model card with no controls and an
    ``available_reason``.
    """
    src_list = list(sources)
    if not src_list:
        return ModelCapabilities()

    merged: dict[str, Any] = {}

    # Lists.
    for key in _LIST_KEYS:
        union: list[str] = []
        seen: set[str] = set()
        any_opinion = False
        for caps in src_list:
            value = caps.get(key)
            if value is None:
                continue
            any_opinion = True
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, str) and item not in seen:
                        seen.add(item)
                        union.append(item)
        if any_opinion:
            merged[key] = union if union else None
        # else: leave key out → ModelCapabilities default of None.

    # Numerics.
    for key in _NUMERIC_KEYS:
        best: int | None = None
        for caps in src_list:
            value = caps.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                if best is None or value > best:
                    best = value
        if best is not None:
            merged[key] = best

    # Booleans.
    for key in _BOOL_KEYS:
        opt_in = False
        any_opinion = False
        for caps in src_list:
            value = caps.get(key)
            if value is None:
                continue
            any_opinion = True
            if value is True:
                opt_in = True
                break
        if any_opinion:
            merged[key] = opt_in

    return ModelCapabilities.model_validate(merged)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_load_caps(raw: str | None) -> dict[str, Any]:
    """Decode capabilities JSON; tolerate corruption with an empty dict."""
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        logger.warning("model_catalog: undecodable capabilities_json")
        return {}
    return value if isinstance(value, dict) else {}


__all__ = (
    "ALL_PROVIDERS_CIRCUIT_OPEN",
    "NO_CAPABLE_PROVIDER",
    "NO_PROVIDER_FOR_TIER",
    "effective_capabilities_for_user",
    "list_models_for_user",
    "validate_primary_adapters",
)


# Re-export for type-checkers / IDE.
_ = AdapterRegistry
