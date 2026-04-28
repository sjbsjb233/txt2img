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

import json
import logging
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
from app.domain.provider_ledger import LedgerError, get_provider_ledger
from app.schemas.provider import (
    ProviderCreate,
    ProviderModelEntry,
    ProviderModelPatch,
    ProviderModelUpdateResponse,
    ProviderModelView,
    ProviderPatch,
    ProviderResponse,
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


async def _build_view(
    session: AsyncSession, provider: Provider
) -> ProviderResponse:
    """Compose the full ``ProviderResponse`` from a provider + joins.

    We accept an open session and reuse it for the joins so list / get
    can stay in a single read transaction.
    """
    models = await _load_models(session, provider.id)
    tiers = await _load_tier_access(session, provider.id)

    try:
        masked = mask_api_key(decrypt(provider.api_key_enc))
    except CryptoError:
        # A row whose ciphertext we can't decrypt is unusable — surface
        # it loudly in the admin UI rather than silently showing "***".
        masked = "<unreadable>"
        logger.error(
            "provider %s has unreadable api_key_enc; check JWT_SECRET",
            provider.id,
        )

    return ProviderResponse(
        id=provider.id,
        label=provider.label,
        adapter_type=provider.adapter_type,
        base_url=provider.base_url,
        api_key_masked=masked,
        cost_per_image_cny=float(provider.cost_per_image_cny),
        balance_cny=float(provider.balance_cny),
        initial_balance_cny=float(provider.initial_balance_cny),
        enabled=bool(provider.enabled),
        note=provider.note,
        max_concurrency=provider.max_concurrency,
        rpm_limit=provider.rpm_limit,
        circuit_state=provider.circuit_state,
        cooldown_until=_isoformat(provider.cooldown_until),
        supported_models=[
            ProviderModelView(
                model_id=m.model_id,
                enabled=bool(m.enabled),
                capabilities=_safe_load_json(m.capabilities_json),
            )
            for m in sorted(models, key=lambda m: m.model_id)
        ],
        tier_access=tiers,
    )


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


@router.get("", response_model=list[ProviderResponse])
async def list_providers(_admin: CurrentAdmin) -> list[ProviderResponse]:
    """Return every provider, sorted by id for deterministic UI ordering.

    Real-time metrics (``metrics_5min``) and richer ``circuit_state``
    transitions arrive in PR-10 / PR-16; today the row is the source of
    truth for the static fields and ``circuit_state`` is whatever was
    last persisted (``healthy`` for fresh rows).
    """
    async with get_session() as session:
        rows = (
            await session.execute(select(Provider).order_by(Provider.id))
        ).scalars().all()
        out: list[ProviderResponse] = []
        for r in rows:
            out.append(await _build_view(session, r))
    return out


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

    async with get_session() as session:
        provider = await _load_provider(session, provider_id)

        # Track what changed for the audit row. Skip api_key value (we
        # only record that it was rotated).
        change_summary: dict[str, Any] = {}

        if "label" in set_fields:
            provider.label = body.label  # type: ignore[assignment]
            change_summary["label"] = body.label
        if "base_url" in set_fields:
            provider.base_url = body.base_url  # type: ignore[assignment]
            change_summary["base_url"] = body.base_url
        if "api_key" in set_fields and body.api_key is not None:
            provider.api_key_enc = encrypt(body.api_key)
            change_summary["api_key"] = "<rotated>"
        if "cost_per_image_cny" in set_fields:
            provider.cost_per_image_cny = float(body.cost_per_image_cny or 0)
            change_summary["cost_per_image_cny"] = provider.cost_per_image_cny
        if "enabled" in set_fields:
            provider.enabled = 1 if body.enabled else 0
            change_summary["enabled"] = bool(body.enabled)
        if "note" in set_fields:
            provider.note = body.note
            change_summary["note"] = body.note
        if "max_concurrency" in set_fields:
            provider.max_concurrency = int(body.max_concurrency or 1)
            change_summary["max_concurrency"] = provider.max_concurrency
        if "rpm_limit" in set_fields:
            provider.rpm_limit = int(body.rpm_limit or 1)
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
        # Load + validate inside the same session as the ledger op so
        # the topup and the audit row commit atomically.
        await _load_provider(session, provider_id)
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
