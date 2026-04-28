"""Admin: list registered adapters (design doc §13.8).

Read-only endpoint. Adapters are discovered at process startup by
``AdapterRegistry.discover()``; the admin UI surfaces them so an admin
adding a provider can pick the right ``adapter_type`` from a known list
and see which providers (if any) currently use each adapter.

We never expose endpoints to *delete* an adapter — adapters are Python
files under ``backend/app/adapters/``. To remove one, drop the file and
restart.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from app.adapters.base import get_registry
from app.db.engine import get_session
from app.db.models import Provider
from app.deps import CurrentAdmin

router = APIRouter(prefix="/api/admin", tags=["admin"])


class AdapterEntry(BaseModel):
    adapter_type: str
    display_name: str
    description: str
    supported_models: list[str]
    in_use_by_providers: list[str]


@router.get("/adapters", response_model=list[AdapterEntry])
async def list_adapters(_admin: CurrentAdmin) -> list[AdapterEntry]:
    """Return every adapter the process has registered.

    ``in_use_by_providers`` is computed from the ``providers`` table on
    each call. Until PR-06 starts populating that table the list is just
    empty for every adapter, which is the intended behaviour.
    """
    registry = get_registry()
    adapters = registry.list_all()

    # Group provider ids by adapter_type in a single query so the admin
    # list scales when there are many providers.
    async with get_session() as session:
        rows = (
            await session.execute(
                select(Provider.id, Provider.adapter_type)
            )
        ).all()
    grouped: dict[str, list[str]] = {}
    for provider_id, adapter_type in rows:
        grouped.setdefault(adapter_type, []).append(provider_id)

    out: list[AdapterEntry] = []
    for adapter in adapters:
        out.append(
            AdapterEntry(
                adapter_type=adapter.adapter_type,
                display_name=adapter.display_name,
                description=adapter.description,
                supported_models=adapter.supported_models(),
                in_use_by_providers=sorted(grouped.get(adapter.adapter_type, [])),
            )
        )
    # Stable order so the admin UI doesn't reshuffle on each refresh.
    out.sort(key=lambda e: e.adapter_type)
    return out
