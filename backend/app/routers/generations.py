"""Image-generation endpoint.

``POST /api/generations`` takes a ``model_key`` + ``station`` selector + the
parameter dict the user filled in on the Create page, picks a routable relay
station (random for ``"auto"``), forwards the call to the matching adapter
client, and returns the generated image(s) base64-encoded.

The endpoint is intentionally a thin wrapper around :mod:`app.dispatcher` —
all of the routing, schema validation, upstream call and balance accounting
lives there. This file is just request shape + error mapping.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user
from ..dispatcher import DispatcherError, dispatch_generate
from ..models import User
from ..schemas import GeneratedImageOut, GenerationIn, GenerationOut

router = APIRouter(prefix="/generations", tags=["generations"])


@router.post("", response_model=GenerationOut)
async def create_generation(
    payload: GenerationIn,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> GenerationOut:
    try:
        result = await dispatch_generate(
            db,
            model_key=payload.model_key,
            station=payload.station,
            prompt=payload.prompt,
            params=payload.params,
            input_images=[img.model_dump() for img in payload.input_images]
            if payload.input_images
            else None,
        )
    except DispatcherError as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message})

    return GenerationOut(
        model_key=result.model_key,
        station_provider_id=result.station_provider_id,
        images=[GeneratedImageOut(mime_type=img.mime_type, b64=img.b64) for img in result.images],
        revised_prompt=result.revised_prompt,
        usage=result.usage,
    )
