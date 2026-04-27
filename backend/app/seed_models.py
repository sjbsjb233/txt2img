"""Default seed for the ``image_models`` catalog.

The site exposes three models today. Each entry's ``param_schema`` is the
*superset* of what the model itself accepts; per-station narrowing lives on
``RelayStationModel.param_capabilities``. See ``param_schema.py`` for the
entry shape.

Boundaries below were cross-referenced against the OpenAI Images and Google
Gemini Image (Imagen) public docs.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from .models import ImageModel

logger = logging.getLogger(__name__)


# Aspect ratios consistently documented across Gemini 3 image preview models.
# The 4 exotic ratios (1:4, 4:1, 1:8, 8:1) some upstreams expose are *not*
# baked in here — a station can opt in via param_capabilities.
_GEMINI_ASPECT_RATIOS = [
    "1:1", "16:9", "9:16", "4:3", "3:4",
    "3:2", "2:3", "5:4", "4:5", "21:9", "9:21",
]

_GEMINI_IMAGE_PARAMS: dict[str, dict[str, Any]] = {
    "aspect_ratio": {
        "type": "enum",
        "values": _GEMINI_ASPECT_RATIOS,
        "default": "1:1",
        "label": "Aspect ratio",
    },
    "image_size": {
        "type": "enum",
        "values": ["512", "1K", "2K", "4K"],
        "default": "1K",
        "label": "Image size",
        "description": "Target longest edge. Some upstreams approximate.",
    },
}


DEFAULT_IMAGE_MODELS: list[dict[str, Any]] = [
    {
        "model_key": "gpt-image-2",
        "label": "ChatGPT Images 2.0",
        "family": "openai_images",
        "sort_order": 0,
        "param_schema": {
            "size": {
                "type": "enum",
                "values": ["1024x1024", "1024x1536", "1536x1024", "auto"],
                "default": "auto",
                "label": "Size",
            },
            "quality": {
                "type": "enum",
                "values": ["auto", "low", "medium", "high"],
                "default": "auto",
                "label": "Quality",
            },
            "n": {
                "type": "int",
                "min": 1,
                "max": 10,
                "step": 1,
                "default": 1,
                "label": "Image count",
            },
            "background": {
                "type": "enum",
                "values": ["auto", "transparent", "opaque"],
                "default": "auto",
                "label": "Background",
            },
            "output_format": {
                "type": "enum",
                "values": ["png", "jpeg", "webp"],
                "default": "png",
                "label": "Output format",
            },
            "output_compression": {
                "type": "int",
                "min": 0,
                "max": 100,
                "step": 1,
                "default": None,
                "label": "Compression",
                "description": "0-100; only applies when output_format is jpeg or webp.",
                "applies_when": {"output_format": ["jpeg", "webp"]},
            },
            "moderation": {
                "type": "enum",
                "values": ["auto", "low"],
                "default": "auto",
                "label": "Moderation",
            },
        },
        "note": (
            "OpenAI native /v1/images/generations. High-tier keys validate size "
            "strictly (longest edge <= 3840); low-tier keys silently fall back "
            "to a 1254x1254 PNG for non-default sizes."
        ),
    },
    {
        "model_key": "gemini-3-pro-image-preview",
        "label": "Gemini 3 Pro Image (Preview)",
        "family": "gemini",
        "sort_order": 10,
        "param_schema": _GEMINI_IMAGE_PARAMS,
        "note": (
            "Gemini native v1beta/.../generateContent. Returns JPEG. Honors "
            "both aspectRatio and imageSize."
        ),
    },
    {
        "model_key": "gemini-3.1-flash-image-preview",
        "label": "Gemini 3.1 Flash Image (Preview)",
        "family": "gemini",
        "sort_order": 20,
        "param_schema": _GEMINI_IMAGE_PARAMS,
        "note": (
            "Faster sibling of Gemini 3 Pro Image. Same control surface; "
            "JPEG output."
        ),
    },
]


def seed_image_models(db: Session) -> None:
    """Insert any of the default image models that don't already exist.

    Uses ``model_key`` as the merge key. Existing rows are left alone — admins
    are expected to edit them through whatever admin UI we end up shipping; we
    don't want to clobber operator changes on every restart.
    """
    existing_keys = {row[0] for row in db.query(ImageModel.model_key).all()}
    inserted = 0
    for entry in DEFAULT_IMAGE_MODELS:
        if entry["model_key"] in existing_keys:
            continue
        db.add(
            ImageModel(
                model_key=entry["model_key"],
                label=entry["label"],
                family=entry["family"],
                sort_order=entry.get("sort_order", 0),
                param_schema=entry.get("param_schema"),
                note=entry.get("note"),
                enabled=True,
            )
        )
        inserted += 1
    if inserted:
        db.commit()
        logger.info("Seeded %d image_models row(s).", inserted)
