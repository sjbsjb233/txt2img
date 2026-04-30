"""Admin endpoints for the ``providers`` (relay station) catalogue.

PR-06 scope (design doc §4 / §13.4 basics):

- Full CRUD on the provider row + its dependent ``provider_models`` and
  ``provider_tier_access`` join tables.
- Topup endpoint (the only ledger-write surface admin owns).
- Per-(provider, model) capability edit and tier-access replace.

Out of scope here (lands in PR-16): live ``test`` ping, ``reset-circuit``,
real-time metrics in the list response, and admin SSE.

Security
--------
- Every route gates on ``CurrentAdmin``; non-admins get 403.
- ``api_key`` is encrypted at rest via ``app.utils.crypto`` and never
  echoed in clear: every response surfaces ``api_key_masked`` instead.
- ``provider_id`` is regex-validated by the pydantic schema so it's
  safe to interpolate into URLs.
- Adapter / model compatibility is checked against the live
  ``AdapterRegistry`` so a typo in ``adapter_type`` fails before the
  row hits the DB.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import get_registry
from app.db.engine import get_session
from app.db.models import (
    Provider,
    ProviderModel,
    ProviderTierAccess,
)
from app.deps import CurrentAdmin
from app.domain.circuit_breaker import get_circuit_breaker
from app.domain.metrics_engine import get_metrics_engine
from app.domain.provider_ledger import LedgerError, get_provider_ledger
from app.schemas.normalized import (
    NormalizedRequest,
    ProviderConfig,
    StandardError,
)
from app.schemas.provider import (
    ProviderCreate,
    ProviderListItem,
    ProviderMetricsView,
    ProviderModelEntry,
    ProviderModelPatch,
    ProviderModelUpdateResponse,
    ProviderModelView,
    ProviderPatch,
    ProviderResetCircuitResponse,
    ProviderResponse,
    ProviderTestRequest,
    ProviderTestResponse,
    ProviderTierAccessUpdate,
    ProviderTopup,
    ProviderTopupResponse,
    TierAccessResponse,
)
from app.utils.audit import write_audit
from app.utils.crypto import CryptoError, decrypt, encrypt, mask_api_key
from app.utils.errors import api_error

logger = logging.getLogger("txt2img.admin.providers")

router = APIRouter(prefix="/api/admin/providers", tags=["admin", "providers"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def _validate_adapter_and_models(
    adapter_type: str,
    supported_models: list[ProviderModelEntry],
) -> None:
    """Reject unknown adapters and models that adapter cannot drive.

    Run *before* we open a transaction so a 422 is cheap. The registry
    is populated at startup; tests that bypass startup must call
    ``AdapterRegistry.discover()`` themselves.
    """
    registry = get_registry()
    if not registry.has(adapter_type):
        raise api_error(
            422,
            "INVALID_PARAMETER",
            f"unknown adapter_type {adapter_type!r}",
            field="adapter_type",
        )
    adapter = registry.get(adapter_type)
    allowed_models = set(adapter.supported_models())

    seen: set[str] = set()
    for entry in supported_models:
        if entry.model_id in seen:
            raise api_error(
                422,
                "INVALID_PARAMETER",
                f"duplicate model_id {entry.model_id!r} in supported_models",
                field="supported_models",
            )
        seen.add(entry.model_id)
        if entry.model_id not in allowed_models:
            raise api_error(
                422,
                "INVALID_PARAMETER",
                f"adapter {adapter_type!r} does not support model "
                f"{entry.model_id!r}; allowed: {sorted(allowed_models)}",
                field="supported_models",
            )


async def _load_provider(
    session: AsyncSession, provider_id: str
) -> Provider:
    """Fetch a provider row or raise 404."""
    row = (
        await session.execute(select(Provider).where(Provider.id == provider_id))
    ).scalar_one_or_none()
    if row is None:
        raise api_error(
            404,
            "NOT_FOUND",
            f"Provider {provider_id!r} does not exist.",
            field="provider_id",
        )
    return row


async def _load_models(
    session: AsyncSession, provider_id: str
) -> list[ProviderModel]:
    return list(
        (
            await session.execute(
                select(ProviderModel).where(ProviderModel.provider_id == provider_id)
            )
        )
        .scalars()
        .all()
    )


async def _load_tier_access(
    session: AsyncSession, provider_id: str
) -> list[str]:
    rows = (
        await session.execute(
            select(ProviderTierAccess.tier).where(
                ProviderTierAccess.provider_id == provider_id
            )
        )
    ).all()
    # Order alphabetically — admin UI doesn't depend on insertion order
    # and a stable order keeps tests / diff views readable.
    return sorted(t for (t,) in rows)


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _base_view_kwargs(
    provider: Provider,
    models: list[ProviderModel],
    tiers: list[str],
) -> dict[str, Any]:
    """Shared field set for both ``ProviderResponse`` and ``ProviderListItem``.

    Decryption + masking lives here so the two response shapes can never
    drift on the masking rule. Returning a dict (rather than a base
    response object the caller would copy fields out of) keeps the call
    sites short.
    """
    try:
        masked = mask_api_key(decrypt(provider.api_key_enc))
    except CryptoError:
        # A row whose ciphertext we can't decrypt is unusable. We log
        # the broken provider id and let the failure propagate so the
        # route returns a 500 instead of a degraded success payload —
        # see ``app/utils/crypto.py``: "callers should propagate this
        # as a 500: a provider row with an undecodable ``api_key_enc``
        # is broken and we can't paper over it."
        logger.exception(
            "provider %s has unreadable api_key_enc; check JWT_SECRET",
            provider.id,
        )
        raise

    return {
        "id": provider.id,
        "label": provider.label,
        "adapter_type": provider.adapter_type,
        "base_url": provider.base_url,
        "api_key_masked": masked,
        "cost_per_image_cny": float(provider.cost_per_image_cny),
        "balance_cny": float(provider.balance_cny),
        "initial_balance_cny": float(provider.initial_balance_cny),
        "enabled": bool(provider.enabled),
        "note": provider.note,
        "max_concurrency": provider.max_concurrency,
        "rpm_limit": provider.rpm_limit,
        "circuit_state": provider.circuit_state,
        "cooldown_until": _isoformat(provider.cooldown_until),
        "supported_models": [
            ProviderModelView(
                model_id=m.model_id,
                enabled=bool(m.enabled),
                capabilities=_safe_load_json(m.capabilities_json),
            )
            for m in sorted(models, key=lambda m: m.model_id)
        ],
        "tier_access": sorted(tiers),
    }


def _view_from_parts(
    provider: Provider,
    models: list[ProviderModel],
    tiers: list[str],
) -> ProviderResponse:
    """Build the basic single-item response (no live metrics)."""
    return ProviderResponse(**_base_view_kwargs(provider, models, tiers))


def _list_item_from_parts(
    provider: Provider,
    models: list[ProviderModel],
    tiers: list[str],
    *,
    rpm_60s: int,
) -> ProviderListItem:
    """Build the list-row response, attaching live MetricsEngine summary.

    Reads from the in-process :class:`MetricsEngine`; no DB hit. The
    snapshot the engine flushes into ``providers.recent_calls_json``
    every 60s is intentionally NOT used here — admin lists should show
    the freshest numbers possible, not whatever the snapshot loop last
    persisted. The persisted snapshot is for cold-start admin views
    *after* a process restart (a future PR), not for the live admin
    page.
    """
    metrics = get_metrics_engine()
    base = _base_view_kwargs(provider, models, tiers)
    metric_views: list[ProviderMetricsView] = []
    window_seconds = max(1, metrics.window_seconds())
    for m in sorted(models, key=lambda m: m.model_id):
        # ``qps * window`` reconstructs the per-(provider, model) call
        # count to one decimal of precision. We round up to the nearest
        # int because admins expect "calls in last 5 min" to be a
        # positive integer when traffic happened.
        qps = metrics.qps(provider.id, m.model_id)
        calls = int(round(qps * window_seconds))
        metric_views.append(
            ProviderMetricsView(
                model_id=m.model_id,
                calls=calls,
                success_rate=metrics.success_rate(provider.id, m.model_id),
                p50_ms=metrics.p50_ms(provider.id, m.model_id),
                p95_ms=metrics.p95_ms(provider.id, m.model_id),
            )
        )
    return ProviderListItem(
        **base,
        current_concurrency=metrics.current_concurrency(provider.id),
        recent_calls_60s=rpm_60s,
        metrics=metric_views,
    )


async def _build_view(
    session: AsyncSession, provider: Provider
) -> ProviderResponse:
    """Single-row variant — issues two extra SELECTs.

    Used by ``get_provider`` / ``create_provider`` / ``patch_provider``
    where the per-row cost is fine. ``list_providers`` does NOT call
    this — it uses ``_view_from_parts`` after bulk-fetching the joins.
    """
    models = await _load_models(session, provider.id)
    tiers = await _load_tier_access(session, provider.id)
    return _view_from_parts(provider, models, tiers)


def _safe_load_json(raw: str) -> dict[str, Any]:
    """Decode capabilities JSON; tolerate corruption with an empty dict.

    A blank dict is the safe fallback because admin can re-PATCH the
    capabilities to repair the row, and downstream code (``/api/models``
    in PR-13) treats "no capabilities" as "no opinion".
    """
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {}
    except (ValueError, TypeError):
        return {}


def _capabilities_to_json(capabilities: Any) -> str:
    """Serialise a ``ProviderModelCapabilities`` (or a dict) for storage."""
    if hasattr(capabilities, "model_dump"):
        data = capabilities.model_dump(exclude_none=True)
    elif isinstance(capabilities, dict):
        data = {k: v for k, v in capabilities.items() if v is not None}
    else:
        data = {}
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


# ---------------------------------------------------------------------------
# List / create
# ---------------------------------------------------------------------------


@router.get("", response_model=list[ProviderListItem])
async def list_providers(_admin: CurrentAdmin) -> list[ProviderListItem]:
    """Return every provider with live runtime telemetry.

    Bulk-fetches ``provider_models`` and ``provider_tier_access`` in two
    extra queries and groups them in Python so the route stays at three
    SELECTs total regardless of provider count. The naive per-row
    variant would be N+1 (one for models + one for tier-access per
    provider) and noticeable as the catalog grows.

    Live metrics (PR-16): ``metrics`` carries a per-model summary over
    the rolling :class:`MetricsEngine` window (currently 5 minutes by
    default), and ``current_concurrency`` / ``recent_calls_60s`` reflect
    in-flight load. Admin UIs that want a "freshness" check should
    refresh on a 5–15s timer.
    """
    async with get_session() as session:
        providers = (
            await session.execute(select(Provider).order_by(Provider.id))
        ).scalars().all()
        if not providers:
            return []

        provider_ids = [p.id for p in providers]
        model_rows = (
            await session.execute(
                select(ProviderModel).where(
                    ProviderModel.provider_id.in_(provider_ids)
                )
            )
        ).scalars().all()
        tier_rows = (
            await session.execute(
                select(
                    ProviderTierAccess.provider_id,
                    ProviderTierAccess.tier,
                ).where(ProviderTierAccess.provider_id.in_(provider_ids))
            )
        ).all()

    models_by_pid: dict[str, list[ProviderModel]] = {pid: [] for pid in provider_ids}
    for m in model_rows:
        models_by_pid.setdefault(m.provider_id, []).append(m)

    tiers_by_pid: dict[str, list[str]] = {pid: [] for pid in provider_ids}
    for pid, tier in tier_rows:
        tiers_by_pid.setdefault(pid, []).append(tier)

    # One bulk pass over the metrics engine for all providers — the
    # ``recent_calls_in_60s_by_provider`` helper avoids the quadratic
    # walk we'd get with per-provider lookups.
    metrics = get_metrics_engine()
    rpm_by_pid = metrics.recent_calls_in_60s_by_provider()

    return [
        _list_item_from_parts(
            p,
            models_by_pid.get(p.id, []),
            tiers_by_pid.get(p.id, []),
            rpm_60s=rpm_by_pid.get(p.id, 0),
        )
        for p in providers
    ]


@router.post("", response_model=ProviderResponse, status_code=201)
async def create_provider(
    body: ProviderCreate,
    admin: CurrentAdmin,
    request: Request,
) -> ProviderResponse:
    """Insert a provider, its model rows and tier access in one transaction.

    Validation order:
        1. Adapter type exists in registry.
        2. Every supplied model is in that adapter's supported list.
        3. ``provider_id`` not already taken (caught by primary-key
           uniqueness; we check explicitly to return a clean 409).
        4. Tier names are valid (already enforced by the schema).
    """
    _validate_adapter_and_models(body.adapter_type, body.supported_models)

    async with get_session() as session:
        existing = (
            await session.execute(
                select(Provider).where(Provider.id == body.provider_id)
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise api_error(
                409,
                "ALREADY_EXISTS",
                f"Provider {body.provider_id!r} already exists.",
                field="provider_id",
            )

        provider = Provider(
            id=body.provider_id,
            label=body.label,
            adapter_type=body.adapter_type,
            base_url=body.base_url,
            api_key_enc=encrypt(body.api_key),
            cost_per_image_cny=body.cost_per_image_cny,
            initial_balance_cny=body.initial_balance_cny,
            balance_cny=body.initial_balance_cny,
            enabled=1 if body.enabled else 0,
            note=body.note,
            max_concurrency=body.max_concurrency,
            rpm_limit=body.rpm_limit,
            circuit_state="healthy",
        )
        session.add(provider)

        for entry in body.supported_models:
            session.add(
                ProviderModel(
                    provider_id=body.provider_id,
                    model_id=entry.model_id,
                    capabilities_json=_capabilities_to_json(entry.capabilities),
                    enabled=1 if entry.enabled else 0,
                )
            )

        for tier in body.tier_access:
            session.add(
                ProviderTierAccess(
                    provider_id=body.provider_id,
                    tier=tier,
                )
            )

        await write_audit(
            session,
            actor_user_id=admin.id,
            action="provider.create",
            target_kind="provider",
            target_id=body.provider_id,
            payload={
                "label": body.label,
                "adapter_type": body.adapter_type,
                "models": [e.model_id for e in body.supported_models],
                "tier_access": body.tier_access,
            },
            ip=_client_ip(request),
        )

        # Flush so the dependent inserts are visible to ``_build_view``
        # which re-selects against the same session.
        await session.flush()
        return await _build_view(session, provider)


# ---------------------------------------------------------------------------
# Single-item read / patch / delete
# ---------------------------------------------------------------------------


@router.get("/{provider_id}", response_model=ProviderResponse)
async def get_provider(
    provider_id: str,
    _admin: CurrentAdmin,
) -> ProviderResponse:
    async with get_session() as session:
        provider = await _load_provider(session, provider_id)
        return await _build_view(session, provider)


@router.patch("/{provider_id}", response_model=ProviderResponse)
async def patch_provider(
    provider_id: str,
    body: ProviderPatch,
    admin: CurrentAdmin,
    request: Request,
) -> ProviderResponse:
    """Partial update of provider basics.

    ``adapter_type`` is intentionally not editable here: changing it
    would silently invalidate every ``provider_models`` row attached.
    Admins who need to switch adapters delete + recreate.
    """
    set_fields = body.model_fields_set
    if not set_fields:
        raise api_error(
            400, "BAD_REQUEST", "PATCH body must contain at least one field."
        )

    # Reject explicit null on non-nullable columns up front so a stray
    # ``{"label": null}`` returns 422 instead of either silently
    # overwriting the row with 0/empty (the previous ``or`` fallback)
    # or crashing the commit on a NOT NULL violation. ``note`` is the
    # only field whose column is nullable; we honour ``null`` there as
    # "clear the note". ``api_key=null`` is also tolerated (it means
    # "no rotation requested").
    _NON_NULLABLE = (
        "label",
        "base_url",
        "cost_per_image_cny",
        "enabled",
        "max_concurrency",
        "rpm_limit",
    )
    for field in _NON_NULLABLE:
        if field in set_fields and getattr(body, field) is None:
            raise api_error(
                422,
                "INVALID_PARAMETER",
                f"{field!r} cannot be null.",
                field=field,
            )

    async with get_session() as session:
        provider = await _load_provider(session, provider_id)

        # Track what changed for the audit row. Skip api_key value (we
        # only record that it was rotated).
        change_summary: dict[str, Any] = {}

        if "label" in set_fields and body.label is not None:
            provider.label = body.label
            change_summary["label"] = body.label
        if "base_url" in set_fields and body.base_url is not None:
            provider.base_url = body.base_url
            change_summary["base_url"] = body.base_url
        if "api_key" in set_fields and body.api_key is not None:
            provider.api_key_enc = encrypt(body.api_key)
            change_summary["api_key"] = "<rotated>"
        if "cost_per_image_cny" in set_fields and body.cost_per_image_cny is not None:
            provider.cost_per_image_cny = float(body.cost_per_image_cny)
            change_summary["cost_per_image_cny"] = provider.cost_per_image_cny
        if "enabled" in set_fields and body.enabled is not None:
            provider.enabled = 1 if body.enabled else 0
            change_summary["enabled"] = bool(body.enabled)
        if "note" in set_fields:
            # Nullable column: ``{"note": null}`` clears the note.
            provider.note = body.note
            change_summary["note"] = body.note
        if "max_concurrency" in set_fields and body.max_concurrency is not None:
            provider.max_concurrency = int(body.max_concurrency)
            change_summary["max_concurrency"] = provider.max_concurrency
        if "rpm_limit" in set_fields and body.rpm_limit is not None:
            provider.rpm_limit = int(body.rpm_limit)
            change_summary["rpm_limit"] = provider.rpm_limit

        provider.updated_at = datetime.now(timezone.utc)

        await write_audit(
            session,
            actor_user_id=admin.id,
            action="provider.update",
            target_kind="provider",
            target_id=provider_id,
            payload={"changes": change_summary},
            ip=_client_ip(request),
        )

        await session.flush()
        return await _build_view(session, provider)


@router.delete("/{provider_id}")
async def delete_provider(
    provider_id: str,
    admin: CurrentAdmin,
    request: Request,
) -> dict[str, bool]:
    """Hard-delete a provider row.

    The schema declares ``ON DELETE CASCADE`` on ``provider_models``
    and ``provider_tier_access``, so removing the parent row also
    removes the children atomically. ``billing_ledger`` is intentionally
    NOT cascaded — we want the audit trail to survive provider removal.
    """
    async with get_session() as session:
        provider = await _load_provider(session, provider_id)
        await session.delete(provider)

        await write_audit(
            session,
            actor_user_id=admin.id,
            action="provider.delete",
            target_kind="provider",
            target_id=provider_id,
            payload=None,
            ip=_client_ip(request),
        )

    return {"ok": True}


# ---------------------------------------------------------------------------
# Per-model capability patch
# ---------------------------------------------------------------------------


@router.patch(
    "/{provider_id}/models/{model_id}",
    response_model=ProviderModelUpdateResponse,
)
async def patch_provider_model(
    provider_id: str,
    model_id: str,
    body: ProviderModelPatch,
    admin: CurrentAdmin,
    request: Request,
) -> ProviderModelUpdateResponse:
    """Update one ``(provider, model)`` row in place.

    The model must already be attached to the provider — adding a new
    model goes via ``PATCH /api/admin/providers/<id>`` extension in PR-16
    or via delete + recreate today (acceptable for v1: capabilities
    decisions don't change often).

    Body semantics:

    - ``capabilities`` (omitted) → keep existing JSON.
    - ``capabilities`` (empty object) → wipe to no opinion.
    - ``enabled`` → toggle availability without losing capabilities.
    """
    set_fields = body.model_fields_set
    if not set_fields:
        raise api_error(
            400, "BAD_REQUEST", "PATCH body must contain at least one field."
        )

    async with get_session() as session:
        await _load_provider(session, provider_id)  # 404 fast-path
        row = (
            await session.execute(
                select(ProviderModel).where(
                    ProviderModel.provider_id == provider_id,
                    ProviderModel.model_id == model_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise api_error(
                404,
                "NOT_FOUND",
                f"Provider {provider_id!r} has no model {model_id!r}.",
                field="model_id",
            )

        if "capabilities" in set_fields and body.capabilities is not None:
            row.capabilities_json = _capabilities_to_json(body.capabilities)
        if "enabled" in set_fields and body.enabled is not None:
            row.enabled = 1 if body.enabled else 0

        await write_audit(
            session,
            actor_user_id=admin.id,
            action="provider.model.update",
            target_kind="provider_model",
            target_id=f"{provider_id}/{model_id}",
            payload={"changes": list(set_fields)},
            ip=_client_ip(request),
        )

        return ProviderModelUpdateResponse(
            provider_id=provider_id,
            model_id=model_id,
            enabled=bool(row.enabled),
            capabilities=_safe_load_json(row.capabilities_json),
        )


# ---------------------------------------------------------------------------
# Tier-access patch (replace-list semantics)
# ---------------------------------------------------------------------------


@router.patch(
    "/{provider_id}/tier-access",
    response_model=TierAccessResponse,
)
async def patch_provider_tier_access(
    provider_id: str,
    body: ProviderTierAccessUpdate,
    admin: CurrentAdmin,
    request: Request,
) -> TierAccessResponse:
    """Replace the provider's tier-access list with the given set.

    Replace, not merge: an empty list means "no tier can use this
    provider". That's deliberately destructive so admins don't have to
    remember which tiers were on before to make the decision explicit.
    """
    async with get_session() as session:
        await _load_provider(session, provider_id)  # 404 fast-path

        # Wipe and re-insert. Two-step here is fine: the table is tiny
        # (≤4 rows per provider) and replacing in place avoids tracking
        # which tiers were added vs removed.
        existing = (
            await session.execute(
                select(ProviderTierAccess).where(
                    ProviderTierAccess.provider_id == provider_id
                )
            )
        ).scalars().all()
        for r in existing:
            await session.delete(r)

        for tier in body.tiers:
            session.add(
                ProviderTierAccess(provider_id=provider_id, tier=tier)
            )

        await write_audit(
            session,
            actor_user_id=admin.id,
            action="provider.tier_access.update",
            target_kind="provider",
            target_id=provider_id,
            payload={"tiers": body.tiers},
            ip=_client_ip(request),
        )

        return TierAccessResponse(
            provider_id=provider_id,
            tiers=sorted(body.tiers),
        )


# ---------------------------------------------------------------------------
# Topup
# ---------------------------------------------------------------------------


@router.post("/{provider_id}/topup", response_model=ProviderTopupResponse)
async def topup_provider(
    provider_id: str,
    body: ProviderTopup,
    admin: CurrentAdmin,
    request: Request,
) -> ProviderTopupResponse:
    """Credit ``amount_cny`` to a provider's balance.

    Side-effects beyond the balance bump are documented on
    ``ProviderLedger.topup``: a ``DRAINED`` provider promotes back to
    ``HEALTHY`` automatically; an ``OPEN`` (faulty) provider does not.

    Audit row records the *requested* amount, not the post-topup
    balance — admins reviewing history care about what they did, not
    where the balance ended up.
    """
    ledger = get_provider_ledger()
    async with get_session() as session:
        # Skip a redundant pre-load: ``ledger.topup`` already raises
        # ``LedgerError`` when the row doesn't exist (its UPDATE
        # returns no row), and we map that to 404 here. This keeps
        # the path to a single SELECT + a single UPDATE.
        try:
            result = await ledger.topup(
                provider_id, body.amount_cny, session=session
            )
        except LedgerError as exc:
            raise api_error(
                404,
                "NOT_FOUND",
                str(exc),
                field="provider_id",
            ) from exc

        await write_audit(
            session,
            actor_user_id=admin.id,
            action="provider.topup",
            target_kind="provider",
            target_id=provider_id,
            payload={
                "amount_cny": body.amount_cny,
                "promoted_from_drained": result.promoted_from_drained,
            },
            ip=_client_ip(request),
        )

        return ProviderTopupResponse(
            provider_id=result.provider_id,
            amount_cny=result.amount_cny,
            balance_after=result.balance_after,
            promoted_from_drained=result.promoted_from_drained,
        )


# ---------------------------------------------------------------------------
# Reset-circuit (PR-16)
# ---------------------------------------------------------------------------


@router.post(
    "/{provider_id}/reset-circuit",
    response_model=ProviderResetCircuitResponse,
)
async def reset_circuit(
    provider_id: str,
    admin: CurrentAdmin,
    request: Request,
) -> ProviderResetCircuitResponse:
    """Force the provider's circuit back to ``HEALTHY``.

    Use this when an upstream you know is fixed is still in cooldown
    (e.g. you fixed the API key, paid a bill, the relay is back). The
    breaker forgets its consecutive-failure count and clears
    ``cooldown_until`` so the next call goes through normally.

    Has no effect on ``DRAINED`` (use topup) or ``DISABLED`` directly,
    but the breaker's :meth:`reset_to_healthy` is intentionally
    unconditional — admin reset is the override hatch and forcing
    ``DRAINED`` back to healthy when balance is genuinely zero will
    immediately re-DRAIN on the next selector pass anyway.
    """
    async with get_session() as session:
        provider = await _load_provider(session, provider_id)

    breaker = get_circuit_breaker()
    new_state = await breaker.reset_to_healthy(provider_id)

    async with get_session() as session:
        await write_audit(
            session,
            actor_user_id=admin.id,
            action="provider.reset_circuit",
            target_kind="provider",
            target_id=provider_id,
            payload={"prior_state": provider.circuit_state},
            ip=_client_ip(request),
        )

    return ProviderResetCircuitResponse(
        provider_id=provider_id,
        circuit_state=new_state,
        cooldown_until=None,
    )


# ---------------------------------------------------------------------------
# Test ping (PR-16)
# ---------------------------------------------------------------------------


# A short, deterministic prompt the test endpoint defaults to. Kept tiny
# so it doesn't burn ledger / produce huge bills, even though we
# explicitly skip the ledger below — the upstream may still bill us.
_TEST_PROMPT_DEFAULT = "A small flat-vector mockup of a yellow banana on white."


@router.post(
    "/{provider_id}/test",
    response_model=ProviderTestResponse,
)
async def test_provider(
    provider_id: str,
    body: ProviderTestRequest,
    admin: CurrentAdmin,
    request: Request,
) -> ProviderTestResponse:
    """Issue exactly one upstream call against ``provider_id``.

    The test endpoint is a *probe*: it tells the admin whether the
    provider answers a real request right now. We deliberately skip:

    * the ledger — successful tests still cost upstream money but we
      don't double-bill the provider's local balance row, and we don't
      record the call in ``billing_ledger``. This is consistent with
      design doc §13.4 ("test → does not count in ledger").
    * the metrics engine — the test result has poor signal-to-noise
      relative to real traffic and we'd rather not pollute the rolling
      success-rate window with an admin-initiated probe.
    * the breaker — see above; we don't want a single admin probe to
      trigger HEALTHY → OPEN even though we DO want admins to *learn*
      the provider just failed. They get the failure in the response.

    A request body is optional: with no ``model_id`` we pick the first
    enabled supported model on the provider; with no ``prompt`` we use
    a tiny built-in default. The response includes the upstream
    latency so admin UIs can flag slow providers.
    """
    async with get_session() as session:
        provider = await _load_provider(session, provider_id)
        models = await _load_models(session, provider_id)

    enabled_models = [m for m in models if m.enabled]
    if not enabled_models:
        raise api_error(
            422,
            "INVALID_PARAMETER",
            "Provider has no enabled supported_models to test.",
            field="model_id",
        )

    if body.model_id is not None:
        chosen = next(
            (m for m in enabled_models if m.model_id == body.model_id),
            None,
        )
        if chosen is None:
            raise api_error(
                422,
                "INVALID_PARAMETER",
                f"Model {body.model_id!r} is not enabled on this provider.",
                field="model_id",
            )
    else:
        chosen = sorted(enabled_models, key=lambda m: m.model_id)[0]

    prompt = body.prompt or _TEST_PROMPT_DEFAULT

    registry = get_registry()
    if not registry.has(provider.adapter_type):
        # Should be impossible (creation validates this), but treat as
        # a 500-class state if a manual DB edit landed an unknown adapter.
        raise api_error(
            500,
            "INVALID_PARAMETER",
            f"Provider has unknown adapter_type {provider.adapter_type!r}.",
            field="adapter_type",
        )
    adapter = registry.get(provider.adapter_type)

    try:
        cleartext_key = decrypt(provider.api_key_enc)
    except CryptoError:
        # Same handling as ``_view_from_parts`` — propagate as 500 via
        # the global exception handler in main.py.
        logger.exception(
            "test: provider %s has unreadable api_key_enc", provider_id
        )
        raise

    config = ProviderConfig(
        id=provider.id,
        base_url=provider.base_url,
        api_key=cleartext_key,
        adapter_type=provider.adapter_type,
        # Short timeout for test pings: admins waiting on the modal
        # don't want to stare at a 2-minute spinner.
        timeout_seconds=30.0,
    )

    norm_request = NormalizedRequest(
        model=chosen.model_id,
        prompt=prompt,
        n=1,
    )

    started = time.monotonic()
    try:
        result = await asyncio.wait_for(
            adapter.generate(config, norm_request),
            timeout=config.timeout_seconds + 5,
        )
        latency_ms = (time.monotonic() - started) * 1000
        ok = result.image_count > 0
        response = ProviderTestResponse(
            provider_id=provider_id,
            model_id=chosen.model_id,
            ok=ok,
            latency_ms=round(latency_ms, 2),
            image_count=result.image_count,
            error_kind=None if ok else "EMPTY_RESPONSE",
            error_message=None if ok else "Upstream returned no image data.",
        )
    except asyncio.TimeoutError:
        latency_ms = (time.monotonic() - started) * 1000
        response = ProviderTestResponse(
            provider_id=provider_id,
            model_id=chosen.model_id,
            ok=False,
            latency_ms=round(latency_ms, 2),
            image_count=0,
            error_kind="UPSTREAM_TIMEOUT",
            error_message=f"Test request exceeded {config.timeout_seconds:.0f}s.",
        )
    except StandardError as exc:
        latency_ms = (time.monotonic() - started) * 1000
        response = ProviderTestResponse(
            provider_id=provider_id,
            model_id=chosen.model_id,
            ok=False,
            latency_ms=round(latency_ms, 2),
            image_count=0,
            error_kind=exc.kind.value,
            error_message=exc.message,
        )
    except Exception as exc:  # pragma: no cover — defensive
        latency_ms = (time.monotonic() - started) * 1000
        logger.exception("test: unexpected error for %s", provider_id)
        response = ProviderTestResponse(
            provider_id=provider_id,
            model_id=chosen.model_id,
            ok=False,
            latency_ms=round(latency_ms, 2),
            image_count=0,
            error_kind="OTHER",
            error_message=f"{type(exc).__name__}: {exc}",
        )

    async with get_session() as session:
        await write_audit(
            session,
            actor_user_id=admin.id,
            action="provider.test",
            target_kind="provider",
            target_id=provider_id,
            payload={
                "model_id": chosen.model_id,
                "ok": response.ok,
                "latency_ms": response.latency_ms,
                "error_kind": response.error_kind,
            },
            ip=_client_ip(request),
        )
    return response
