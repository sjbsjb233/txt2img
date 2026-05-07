"""Generated test assets for the provider test-suite C套件.

We synthesise small, neutral geometric images at first use rather than
checking binary blobs into git. The shapes are deliberately abstract
(triangles, rounded rectangles, alpha masks) so they do not trip
moderation filters on real upstreams while still being recognisable
enough that an admin can tell whether a reference image was honoured.

All assets are written under the running ``DATA_ROOT`` (per
``app.config.Settings``) so test runs and unit tests do not pollute the
checked-in source tree. Each loader function returns ``(bytes, mime,
filename)`` so callers can drop them straight into the multipart payload
or into a ``NormalizedReference``.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import NamedTuple

from PIL import Image, ImageDraw

from app.config import get_settings


class Asset(NamedTuple):
    data: bytes
    mime: str
    filename: str


def _cache_dir() -> Path:
    root = Path(get_settings().DATA_ROOT).resolve() / "provider_tests" / "_assets"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _read_or_make(name: str, mime: str, factory) -> Asset:
    path = _cache_dir() / name
    if path.exists():
        try:
            return Asset(path.read_bytes(), mime, name)
        except OSError:
            pass
    img: Image.Image = factory()
    buf = BytesIO()
    fmt = "PNG" if mime == "image/png" else "JPEG"
    save_kwargs: dict = {}
    if fmt == "JPEG":
        save_kwargs["quality"] = 88
    img.save(buf, format=fmt, **save_kwargs)
    data = buf.getvalue()
    try:
        path.write_bytes(data)
    except OSError:
        pass
    return Asset(data, mime, name)


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def _make_logo() -> Image.Image:
    img = Image.new("RGB", (512, 512), color=(244, 239, 230))
    draw = ImageDraw.Draw(img)
    draw.polygon(
        [(256, 64), (456, 432), (56, 432)],
        fill=(244, 196, 48),
        outline=(25, 23, 20),
    )
    draw.ellipse((216, 224, 296, 304), fill=(25, 23, 20))
    return img


def _make_product() -> Image.Image:
    img = Image.new("RGB", (768, 768), color=(237, 230, 214))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((128, 256, 640, 640), radius=32,
                           fill=(63, 93, 42), outline=(25, 23, 20), width=4)
    draw.ellipse((300, 120, 468, 288), fill=(244, 196, 48),
                 outline=(25, 23, 20), width=4)
    return img


def _make_edit_base() -> Image.Image:
    img = Image.new("RGB", (1024, 1024), color=(244, 239, 230))
    draw = ImageDraw.Draw(img)
    # Left half: deep ink rectangle.
    draw.rectangle((64, 64, 480, 960), fill=(25, 23, 20))
    # Right half: soft banana rectangle.
    draw.rectangle((544, 64, 960, 960), fill=(251, 233, 161))
    return img


def _make_edit_mask() -> Image.Image:
    """Right-half-transparent alpha mask (right side editable)."""
    img = Image.new("RGBA", (1024, 1024), color=(0, 0, 0, 255))
    draw = ImageDraw.Draw(img)
    # Right half α=0 -> editable.
    draw.rectangle((512, 0, 1024, 1024), fill=(0, 0, 0, 0))
    return img


def _make_geometry() -> Image.Image:
    img = Image.new("RGB", (512, 512), color=(244, 239, 230))
    draw = ImageDraw.Draw(img)
    draw.regular_polygon((256, 256, 200), n_sides=6,
                         fill=(217, 164, 0), outline=(25, 23, 20))
    return img


# ---------------------------------------------------------------------------
# Public loaders
# ---------------------------------------------------------------------------


def load_ref_logo() -> Asset:
    return _read_or_make("ref_logo.png", "image/png", _make_logo)


def load_ref_product() -> Asset:
    return _read_or_make("ref_product.jpg", "image/jpeg", _make_product)


def load_edit_base() -> Asset:
    return _read_or_make("edit_base.png", "image/png", _make_edit_base)


def load_edit_mask() -> Asset:
    return _read_or_make("edit_mask.png", "image/png", _make_edit_mask)


def load_geometry() -> Asset:
    return _read_or_make("ref_geometry.png", "image/png", _make_geometry)
