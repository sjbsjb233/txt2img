"""Model catalog for the Create page.

``GET /api/models`` returns every enabled ``image_models`` row plus the list of
relay stations that serve it. Each station entry carries the ``effective``
parameter schema (model schema overlaid with station narrowing) so the frontend
can render controls without re-implementing the merge logic.

The endpoint requires a logged-in user but doesn't expose API keys or any other
station secret — only ``provider_id``, display label, balance and the param
schemas.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, selectinload

from ..db import get_db
from ..deps import get_current_user
from ..models import ImageModel, RelayStationModel, User
from ..param_schema import merge_param_schema
from ..schemas import ModelCatalogOut, ModelOption, StationOption

router = APIRouter(prefix="/models", tags=["models"])


@router.get("", response_model=ModelCatalogOut)
def list_models(
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> ModelCatalogOut:
    rows = (
        db.query(ImageModel)
        .options(
            selectinload(ImageModel.station_bindings).selectinload(
                RelayStationModel.relay_station
            )
        )
        .filter(ImageModel.enabled.is_(True))
        .order_by(ImageModel.sort_order.asc(), ImageModel.id.asc())
        .all()
    )

    catalog: list[ModelOption] = []
    for model in rows:
        stations: list[StationOption] = []
        for binding in model.station_bindings:
            station = binding.relay_station
            if not (binding.enabled and station.enabled):
                continue
            effective = merge_param_schema(model.param_schema, binding.param_capabilities)
            stations.append(
                StationOption(
                    provider_id=station.provider_id,
                    label=station.label,
                    cost_per_image_cny=binding.cost_per_image_cny
                    or station.cost_per_image_cny,
                    balance_cny=station.balance_cny,
                    param_capabilities=binding.param_capabilities,
                    effective_param_schema=effective,
                )
            )

        catalog.append(
            ModelOption(
                id=model.id,
                model_key=model.model_key,
                label=model.label,
                family=model.family,
                sort_order=model.sort_order,
                note=model.note,
                param_schema=model.param_schema,
                stations=stations,
            )
        )

    return ModelCatalogOut(models=catalog)
