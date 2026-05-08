"""Outpaint canvas + mask synthesis (front-end §13).

gpt-image-2 has no native "extend canvas in this direction" parameter —
its only edit endpoint is ``/v1/images/edits``, which takes a single
image plus an optional mask. We synthesise the outpaint geometry on
this side of the wire:

1. Take the parent's source image (the one the user clicked "edit"
   on) as input.
2. Build a new, larger canvas by placing that image in the right
   anchor position, padded by transparent pixels in the requested
   directions.
3. Build a matching mask: the original-image region keeps alpha=255
   ("preserve"), the extended region gets alpha=0 ("repaint").
4. If the user also painted an inpaint mask in the editor, union it
   with the synthesised one so a single submit can do both at once.

The output of :func:`synthesize_outpaint` is a ``(canvas_png_bytes,
mask_png_bytes, new_size)`` triple ready to be fed back into the
mask-edit code path. The route layer is responsible for actually
calling this and substituting the user's reference + mask before
enqueueing the job.

Pure-Pillow + io implementation. No async I/O, no network — safe to
call from the executor's worker thread.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from io import BytesIO
from typing import Iterable

from PIL import Image

logger = logging.getLogger("txt2img.outpaint")


@dataclass(frozen=True)
class OutpaintGeometry:
    """Resolved per-direction extension in pixels."""

    left: int = 0
    right: int = 0
    top: int = 0
    bottom: int = 0

    @property
    def total_width_extra(self) -> int:
        return self.left + self.right

    @property
    def total_height_extra(self) -> int:
        return self.top + self.bottom

    def applied_to(self, w: int, h: int) -> tuple[int, int]:
        return (w + self.total_width_extra, h + self.total_height_extra)


def parse_outpaint_amount(amount: str) -> tuple[str, int]:
    """Parse '25%' / '256px' -> ('percent', 25) / ('pixels', 256)."""
    a = (amount or "").strip().lower()
    if a.endswith("%"):
        try:
            value = int(a[:-1])
        except ValueError as exc:
            raise ValueError(f"invalid outpaint amount: {amount!r}") from exc
        if value <= 0 or value > 400:
            raise ValueError(
                f"outpaint percent must be in 1..400, got {value}"
            )
        return ("percent", value)
    if a.endswith("px"):
        try:
            value = int(a[:-2])
        except ValueError as exc:
            raise ValueError(f"invalid outpaint amount: {amount!r}") from exc
        if value <= 0 or value > 4096:
            raise ValueError(
                f"outpaint pixels must be in 1..4096, got {value}"
            )
        return ("pixels", value)
    raise ValueError(
        f"outpaint amount must end with '%' or 'px', got {amount!r}"
    )


def resolve_geometry(
    image_w: int,
    image_h: int,
    directions: Iterable[str],
    amount: str,
) -> OutpaintGeometry:
    """Compute how many pixels to extend in each requested direction."""
    kind, value = parse_outpaint_amount(amount)
    dirs = {d.lower() for d in directions}
    if not dirs:
        raise ValueError("outpaint requires at least one direction")
    invalid = dirs - {"left", "right", "top", "bottom"}
    if invalid:
        raise ValueError(f"unknown outpaint direction(s): {sorted(invalid)}")

    # Base reference for percent: use the relevant axis length.
    def px_for(axis_len: int) -> int:
        if kind == "pixels":
            return value
        return max(1, round(axis_len * value / 100))

    return OutpaintGeometry(
        left=px_for(image_w) if "left" in dirs else 0,
        right=px_for(image_w) if "right" in dirs else 0,
        top=px_for(image_h) if "top" in dirs else 0,
        bottom=px_for(image_h) if "bottom" in dirs else 0,
    )


def synthesize_outpaint(
    *,
    source_png_bytes: bytes,
    directions: Iterable[str],
    amount: str,
    extra_mask_png_bytes: bytes | None = None,
) -> tuple[bytes, bytes, tuple[int, int]]:
    """Build the extended canvas + matching mask.

    Returns ``(canvas_png_bytes, mask_png_bytes, (new_w, new_h))``. The
    canvas is always RGBA so OpenAI's edits endpoint can blend; the
    mask is RGBA where alpha=255 means "preserve" and alpha=0 means
    "repaint" (matching the editor's contract — see frontend §7.8).

    ``extra_mask_png_bytes`` is the user's inpaint mask painted on the
    *original* image (before extension). If supplied, it is composited
    onto the synthesised mask in the original-image region: any pixel
    the user painted to 0 stays at 0 in the final mask.
    """
    src = Image.open(BytesIO(source_png_bytes)).convert("RGBA")
    sw, sh = src.size
    geom = resolve_geometry(sw, sh, directions, amount)
    new_w, new_h = geom.applied_to(sw, sh)

    canvas = Image.new("RGBA", (new_w, new_h), (0, 0, 0, 0))
    canvas.paste(src, (geom.left, geom.top))

    # Build mask: start fully transparent (=0 = repaint) then stamp
    # an opaque white block over the original-image area (=255 = keep).
    mask = Image.new("RGBA", (new_w, new_h), (0, 0, 0, 0))
    keep_block = Image.new("RGBA", (sw, sh), (255, 255, 255, 255))
    mask.paste(keep_block, (geom.left, geom.top))

    if extra_mask_png_bytes is not None:
        extra = Image.open(BytesIO(extra_mask_png_bytes)).convert("RGBA")
        if extra.size != (sw, sh):
            logger.warning(
                "outpaint: discarding inpaint mask — size %s != source %s",
                extra.size,
                (sw, sh),
            )
        else:
            # Take the alpha channel of the user's mask. Where alpha is
            # 0 in the user's mask, force 0 in the synthesised one too.
            user_alpha = extra.split()[3]
            target_alpha = mask.split()[3]
            new_alpha = Image.new("L", (new_w, new_h), 0)
            new_alpha.paste(target_alpha, (0, 0))
            offset = (geom.left, geom.top)
            # paste with mask=user_alpha so only "kept" user-painted area
            # is multiplied through.
            cropped_target = new_alpha.crop(
                (geom.left, geom.top, geom.left + sw, geom.top + sh)
            )
            mixed = Image.eval(
                Image.merge(
                    "L",
                    (
                        Image.eval(
                            Image.composite(
                                cropped_target,
                                Image.new("L", (sw, sh), 0),
                                user_alpha,
                            ),
                            lambda v: v,
                        ),
                    ),
                ).split()[0],
                lambda v: v,
            )
            new_alpha.paste(mixed, offset)
            mask.putalpha(new_alpha)

    canvas_buf = BytesIO()
    canvas.save(canvas_buf, format="PNG")
    mask_buf = BytesIO()
    mask.save(mask_buf, format="PNG")
    return canvas_buf.getvalue(), mask_buf.getvalue(), (new_w, new_h)


__all__ = (
    "OutpaintGeometry",
    "parse_outpaint_amount",
    "resolve_geometry",
    "synthesize_outpaint",
)
