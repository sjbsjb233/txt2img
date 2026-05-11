"""Provider test-suite orchestration.

Drives the per-model test matrix from :mod:`app.domain.test_cases`,
streams results back to the admin via SSE, persists generated images to
``DATA_ROOT/provider_tests/<provider_id>/<run_id>/`` for short-lived
download, and exposes a manual-verdict hook for SEMI / MANUAL cases.

Importantly, the runner intentionally bypasses ledger / metrics /
circuit-breaker state — same semantics as the legacy ``POST /test``
probe (design doc §13.4).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import time
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from PIL import Image, ImageChops, ImageFilter, ImageMath, ImageStat

from app.adapters.base import BaseAdapter
from app.config import get_settings
from app.domain.test_cases import TestCase, get_matrix
from app.domain.test_judges import (
    Verdict,
    aspect_close,
    expected_error_kinds,
    magic_matches,
    probe_image,
)
from app.schemas.normalized import (
    NormalizedImage,
    NormalizedRequest,
    NormalizedResponse,
    ProviderConfig,
    StandardError,
)


logger = logging.getLogger("txt2img.test_suite")


# ---------------------------------------------------------------------------
# Run state — kept in-process, accessed by image fetch + verdict routes
# ---------------------------------------------------------------------------


@dataclass
class _StoredImage:
    case_id: str
    idx: int
    mime: str
    width: int
    height: int
    byte_size: int
    file_path: Path


@dataclass
class _StoredDiagnostic:
    """Auxiliary file attached to a case for human review.

    Currently used by mask cases (heatmap + overlay) but the shape is
    generic so future judges can attach their own artifacts.
    """

    name: str
    label: str
    kind: str  # "heatmap" | "overlay"
    mime: str
    byte_size: int
    file_path: Path


@dataclass
class _CaseResult:
    case_id: str
    suite: str
    title: str
    judge_level: str
    cost_image: bool
    ok: bool
    auto_verdict: list[dict[str, Any]]
    manual_required: bool
    manual_prompt: str | None
    manual_verdict: str | None  # "pass" / "fail" / "skip"
    error_kind: str | None
    error_message: str | None
    latency_ms: float | None
    images: list[_StoredImage]
    diagnostics: list[_StoredDiagnostic] = field(default_factory=list)
    mask_metrics: dict[str, float] | None = None


@dataclass
class _RunState:
    run_id: str
    provider_id: str
    model_id: str
    started_at: float
    cases: dict[str, _CaseResult] = field(default_factory=dict)
    skipped_by_capability: list[str] = field(default_factory=list)
    finished: bool = False
    verdict: str | None = None
    actor_id: str | None = None
    cost_used: int = 0


_RUN_TTL_SECONDS = 60 * 60  # design says 1h
_runs: dict[str, _RunState] = {}


def _purge_expired() -> None:
    """Drop runs older than the TTL plus their on-disk artifacts.

    Called opportunistically from ``get_run`` and other accessors. We
    deliberately don't run a background thread for this — the TTL is
    soft and an extra 1h of stale bytes on disk is fine compared to
    the cost of a periodic sweeper. ``shutil.rmtree`` is best-effort:
    we swallow OS errors so a missing dir or permission glitch can't
    knock out a still-live run lookup.
    """
    now = time.time()
    stale = [rid for rid, r in _runs.items() if now - r.started_at > _RUN_TTL_SECONDS]
    for rid in stale:
        run = _runs.pop(rid, None)
        if run is None:
            continue
        try:
            run_dir = _provider_tests_root() / run.provider_id / rid
            if run_dir.exists():
                shutil.rmtree(run_dir, ignore_errors=True)
        except Exception:  # pragma: no cover — defensive
            logger.debug("test_suite: failed to scrub %s", rid, exc_info=True)


def get_run(run_id: str) -> _RunState | None:
    _purge_expired()
    return _runs.get(run_id)


# ---------------------------------------------------------------------------
# Image URL signing
# ---------------------------------------------------------------------------
#
# Persisted test-suite images are served from a same-origin route the
# admin browser can hit directly via ``<img src=...>``. We can't rely on
# the admin's bearer token there: the browser only carries it on
# fetch()-style requests, not on raw asset loads. Instead the runner
# stamps every emitted ``bytes_url`` with a short-lived HMAC signature
# that the route validates server-side. Same TTL as the run itself, so
# the URLs naturally expire when the run does.
#
# Key derivation: HKDF-style hash off ``JWT_SECRET`` so we don't have to
# add another env var. ``JWT_SECRET`` is required at startup; if it's
# empty we fail closed (returns "" — verification will reject every
# signature).


def _signing_key() -> bytes:
    secret = os.environ.get("JWT_SECRET", "")
    if not secret:
        return b""
    return hashlib.sha256(b"provider_test_suite_image_v1|" + secret.encode()).digest()


def _sign_payload(provider_id: str, run_id: str, name: str, exp: int) -> str:
    """Produce a URL-safe base64 HMAC for ``(provider, run, name, exp)``."""
    key = _signing_key()
    if not key:
        return ""
    msg = f"{provider_id}|{run_id}|{name}|{exp}".encode("utf-8")
    digest = hmac.new(key, msg, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def verify_image_signature(
    provider_id: str, run_id: str, name: str, sig: str, exp: int
) -> bool:
    """True iff ``sig`` is a fresh, valid signature for the tuple.

    Time check uses ``int(time.time())`` so callers can deterministically
    reproduce the boundary in tests.
    """
    if not sig or not exp or exp < int(time.time()):
        return False
    expected = _sign_payload(provider_id, run_id, name, exp)
    if not expected:
        return False
    return hmac.compare_digest(expected, sig)


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------


def _provider_tests_root() -> Path:
    root = Path(get_settings().DATA_ROOT).resolve() / "provider_tests"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _run_dir(provider_id: str, run_id: str) -> Path:
    p = _provider_tests_root() / provider_id / run_id
    p.mkdir(parents=True, exist_ok=True)
    return p


_SAFE_NAME = re.compile(r"^[a-zA-Z0-9_]+\.[a-z]{3,4}$")


def resolve_image_path(provider_id: str, run_id: str, name: str) -> Path | None:
    """Return the image file path inside the run dir, or None if invalid.

    ``name`` is path-traversal-safe: we restrict to ``[A-Za-z0-9_].ext``
    and verify the resolved path stays under the run dir.
    """
    if not _SAFE_NAME.match(name):
        return None
    base = _run_dir(provider_id, run_id)
    target = (base / name).resolve()
    if not str(target).startswith(str(base.resolve())):
        return None
    if not target.exists():
        return None
    return target


# ---------------------------------------------------------------------------
# Capability filter — drop cases the provider didn't enable.
# ---------------------------------------------------------------------------


def _filter_by_capabilities(
    cases: list[TestCase], capabilities: dict[str, Any]
) -> tuple[list[TestCase], list[str]]:
    """Return (kept_cases, dropped_case_ids).

    Currently we only respect ``stream``: openai C4 (not yet in matrix)
    requires ``capabilities.stream == True``. For now this is a no-op
    pass-through; the hook is here so we can extend without rewiring
    the runner.
    """
    skipped: list[str] = []
    kept: list[TestCase] = []
    stream_ok = bool(capabilities.get("stream"))
    for case in cases:
        # Reserve hook for future capability gating; nothing currently
        # blocked unless a case asked for stream and the provider didn't.
        if case.case_id == "C4" and not stream_ok:
            skipped.append(case.case_id)
            continue
        kept.append(case)
    return kept, skipped


# ---------------------------------------------------------------------------
# Judges — graded against a NormalizedResponse + the request that built
# it. Each returns (ok, verdicts).
# ---------------------------------------------------------------------------


def _judge_basic_image(
    request: NormalizedRequest, response: NormalizedResponse
) -> tuple[bool, list[Verdict]]:
    verdicts: list[Verdict] = []
    ok = True
    if response.image_count <= 0:
        ok = False
        verdicts.append(Verdict(False, "未返回任何图片"))
        return ok, verdicts
    verdicts.append(
        Verdict(True, f"image_count = {response.image_count}, 数据成功解码")
    )
    return ok, verdicts


def _judge_openai_a1(
    request: NormalizedRequest, response: NormalizedResponse
) -> tuple[bool, list[Verdict]]:
    return _judge_basic_image(request, response)


def _judge_openai_b1(
    request: NormalizedRequest, response: NormalizedResponse
) -> tuple[bool, list[Verdict]]:
    verdicts: list[Verdict] = []
    ok = True
    if response.image_count != 2:
        ok = False
        verdicts.append(Verdict(False, f"期望返回 2 张图,实际 {response.image_count} 张"))
    else:
        verdicts.append(Verdict(True, "返回了 2 张图"))

    for idx, img in enumerate(response.images):
        info = probe_image(img)
        size_ok = info["width"] == 1024 and info["height"] == 1536
        verdicts.append(
            Verdict(
                size_ok,
                f"image[{idx}] {info['width']}×{info['height']} "
                f"(期望 1024×1536)",
            )
        )
        if not size_ok:
            ok = False
        mime_ok = magic_matches(img.data, "image/jpeg")
        verdicts.append(
            Verdict(
                mime_ok, f"image[{idx}] 文件魔数符合 JPEG: {mime_ok}"
            )
        )
        if not mime_ok:
            ok = False
    return ok, verdicts


def _judge_openai_b2(
    request: NormalizedRequest, response: NormalizedResponse
) -> tuple[bool, list[Verdict]]:
    verdicts: list[Verdict] = []
    ok = response.image_count > 0
    verdicts.append(
        Verdict(ok, f"size=auto 调用成功, image_count={response.image_count}")
    )
    if response.images:
        info = probe_image(response.images[0])
        verdicts.append(
            Verdict(
                info["width"] > 0,
                f"返回图尺寸 {info['width']}×{info['height']} (auto 不强校验)",
            )
        )
    return ok, verdicts


def _judge_openai_b3(
    request: NormalizedRequest, response: NormalizedResponse
) -> tuple[bool, list[Verdict]]:
    verdicts: list[Verdict] = []
    ok = response.image_count > 0
    if response.images:
        img = response.images[0]
        info = probe_image(img)
        size_ok = info["width"] == 1280 and info["height"] == 768
        verdicts.append(
            Verdict(
                size_ok,
                f"返回图 {info['width']}×{info['height']} (期望 1280×768)",
            )
        )
        webp_ok = magic_matches(img.data, "image/webp")
        verdicts.append(Verdict(webp_ok, f"WebP 文件魔数: {webp_ok}"))
        if not size_ok or not webp_ok:
            ok = False
    return ok, verdicts


def c3_left_similarity(base_bytes: bytes, out_bytes: bytes) -> float:
    """Left-half similarity for the C3 mask test.

    The earlier implementation suffered from two off-by-percent bugs:

    1. Cropping in absolute pixels (``min(w//2, w'//2)``) selected
       different proportional regions when the relay rescaled the
       output (e.g. 1024 → 1254), so we ended up comparing ~50% of
       base against ~40% of output.
    2. The two crops had different aspect ratios, so the subsequent
       resize to a common 128×128 canvas distorted them by different
       amounts and shifted the dark rectangle's right edge by 2-3 px.

    The fix: normalise BOTH images to a common 256×256 canvas first,
    then crop each to the left half (128×256). The two regions are
    now exactly the same proportional area of their source image and
    no aspect-ratio drift. Real relays land in 60-90% with this
    metric — anything <50% strongly indicates the mask was ignored.
    """
    with Image.open(BytesIO(base_bytes)) as base_img, Image.open(
        BytesIO(out_bytes)
    ) as out_img:
        base = base_img.convert("RGB").resize((256, 256))
        out = out_img.convert("RGB").resize((256, 256))
    base_left = base.crop((0, 0, 128, 256))
    out_left = out.crop((0, 0, 128, 256))
    diff = 0
    for a, b in zip(base_left.getdata(), out_left.getdata()):
        if abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2]) > 30:
            diff += 1
    total = 128 * 256
    return max(0.0, 1.0 - diff / total)


# Threshold below which we treat the relay as having clearly ignored
# the mask (left half regenerated). Real relays sit in 60-90%; only a
# total disregard for the alpha mask drops below this.
_C3_SIMILARITY_FLOOR = 0.50


def _judge_openai_c3(
    request: NormalizedRequest, response: NormalizedResponse
) -> tuple[bool, list[Verdict]]:
    """SEMI judge for the openai mask edit case.

    Per design v2 §5.2 — SEMI judges never auto-fail when an image
    is returned. The similarity ratio is surfaced as an *informational*
    verdict bullet so the admin can sanity-check whether the relay
    honored the mask, but the case still advances to manual_pending
    for human review. Only "no image at all" causes ok=False.
    """
    verdicts: list[Verdict] = []
    if response.image_count <= 0:
        verdicts.append(Verdict(False, "未返回任何图片"))
        return False, verdicts

    info = probe_image(response.images[0])
    verdicts.append(
        Verdict(True, f"返回图 {info['width']}×{info['height']}")
    )

    try:
        from app.resources.test_assets import load_edit_base

        ratio = c3_left_similarity(load_edit_base().data, response.images[0].data)
        if ratio >= _C3_SIMILARITY_FLOOR:
            verdicts.append(
                Verdict(
                    True,
                    f"左半区像素相似度 ≈ {ratio:.0%}(>{int(_C3_SIMILARITY_FLOOR*100)}% 视为 mask 大概率工作了 · 仅供参考)",
                )
            )
        else:
            verdicts.append(
                Verdict(
                    False,
                    f"左半区像素相似度 ≈ {ratio:.0%}(<{int(_C3_SIMILARITY_FLOOR*100)}% — 中转可能忽略了 mask · 仅供参考)",
                )
            )
    except Exception as exc:  # pragma: no cover — defensive
        verdicts.append(Verdict(True, f"相似度计算跳过: {exc!r}"))

    # SEMI 用例:有图就交给人看,auto 不一票否决。
    return True, verdicts


# ---------------------------------------------------------------------------
# Mask plan v2 — quantitative judges for M1..M8
#
# Both judges:
#   * resize the upstream output to the canonical mask geometry so
#     per-pixel comparison is exact, regardless of relay rescaling
#   * use the native alpha mask to split the image into "preserve" and
#     "edit" regions (alpha=255 → preserve, alpha=0 → edit)
#   * compute the metrics from §5.1/§5.2 of the design doc and emit a
#     diff heatmap diagnostic so the admin can review failures visually
# ---------------------------------------------------------------------------


# Per-case context. The judge receives ``request`` + ``response`` but not
# ``case_id``, so the runner uses this table to dispatch to a closure
# carrying the right target / scenario (mirrors ``_judge_gemini_aspect``
# but keyed by case id rather than by a hard-coded constant in the judge
# registry).
_MASK_CASE_CTX: dict[str, dict[str, str]] = {
    "M1": {"kind": "inpaint", "target": "villager", "method": "native"},
    "M2": {"kind": "inpaint", "target": "villager", "method": "fallback"},
    "M3": {"kind": "inpaint", "target": "iron_golem", "method": "native"},
    "M4": {"kind": "inpaint", "target": "iron_golem", "method": "fallback"},
    "M5": {"kind": "outpaint", "scenario": "right", "method": "native"},
    "M6": {"kind": "outpaint", "scenario": "right", "method": "fallback"},
    "M7": {"kind": "outpaint", "scenario": "bottom", "method": "native"},
    "M8": {"kind": "outpaint", "scenario": "bottom", "method": "fallback"},
}

MASK_CASE_IDS: tuple[str, ...] = tuple(_MASK_CASE_CTX.keys())


def _diff_magnitude(scene_rgb: Image.Image, out_rgb: Image.Image) -> Image.Image:
    """Per-pixel ``L1`` colour distance, normalised to mode ``L``.

    Pixel value is ``(|ΔR|+|ΔG|+|ΔB|)/3`` clamped to ``0..255``. We use
    L1 rather than L2 because PIL has no per-pixel sqrt and the absolute
    delta is monotonic with the L2 distance for the thresholds we care
    about (mean ≪ 1.0 vs ≪ 0.05).
    """

    diff = ImageChops.difference(scene_rgb, out_rgb)
    r, g, b = diff.split()
    # ``ImageMath.lambda_eval`` replaced ``ImageMath.eval`` in Pillow 11
    # (the older API is being removed in Pillow 12). Cast back to "L" so
    # downstream masks / stat helpers behave.
    return ImageMath.lambda_eval(
        lambda args: args["convert"]((args["r"] + args["g"] + args["b"]) / 3, "L"),
        {"r": r, "g": g, "b": b},
    )


def _split_regions(mask_rgba: Image.Image) -> tuple[Image.Image, Image.Image]:
    """Return ``(preserve_mask_L, edit_mask_L)`` from a native alpha mask.

    PIL's ``ImageStat.Stat(image, mask)`` includes pixels where mask is
    non-zero. So:

    * ``preserve_mask_L``: 255 where alpha == 255 (preserve), 0 elsewhere
    * ``edit_mask_L``:     255 where alpha == 0   (edit), 0 elsewhere
    """

    alpha = mask_rgba.split()[-1]  # the alpha channel as mode "L"
    preserve = alpha.point(lambda v: 255 if v == 255 else 0)
    edit = alpha.point(lambda v: 255 if v == 0 else 0)
    return preserve, edit


def _region_mean(diff_L: Image.Image, region_L: Image.Image) -> float:
    """Mean of ``diff_L`` over pixels where ``region_L`` is non-zero.

    Returns 0.0 when the region is empty so callers don't have to guard.
    """

    stat = ImageStat.Stat(diff_L, mask=region_L)
    if not stat.count or stat.count[0] == 0:
        return 0.0
    return float(stat.mean[0]) / 255.0


def _region_pixel_count(region_L: Image.Image) -> int:
    """Number of non-zero pixels in ``region_L``."""

    # PIL stores histogram as a 256-length list; index 0 is the count of
    # zero pixels, everything else is "covered by the mask".
    hist = region_L.histogram()
    return sum(hist[1:])


def _make_diff_heatmap(
    diff_L: Image.Image, edit_region: Image.Image | None = None
) -> bytes:
    """Render a coloured PNG heatmap from the diff magnitude image.

    * pixels with magnitude below 13 (≈5% of 255) are transparent so the
      heatmap reads as "where did the model change things" rather than a
      blanket overlay
    * the visible range is mapped via a hand-rolled jet-ish palette
      (blue → cyan → green → yellow → red) without numpy
    """

    width, height = diff_L.size
    src = diff_L.tobytes()
    rgba = bytearray(width * height * 4)
    for i, v in enumerate(src):
        if v < 13:
            continue  # leave transparent
        # Jet-ish ramp on [13, 255]
        t = (v - 13) / (255 - 13)
        if t < 0.25:
            r, g, b = 0, int(255 * (t / 0.25)), 255
        elif t < 0.5:
            r, g, b = 0, 255, int(255 * (1 - (t - 0.25) / 0.25))
        elif t < 0.75:
            r, g, b = int(255 * ((t - 0.5) / 0.25)), 255, 0
        else:
            r, g, b = 255, int(255 * (1 - (t - 0.75) / 0.25)), 0
        off = i * 4
        rgba[off] = r
        rgba[off + 1] = g
        rgba[off + 2] = b
        rgba[off + 3] = 220
    heat = Image.frombytes("RGBA", (width, height), bytes(rgba))
    if edit_region is not None:
        # Mask the heatmap to the edit region so we don't mislead the
        # admin into thinking pixel noise outside the mask is a problem.
        heat.putalpha(
            ImageChops.multiply(
                heat.split()[-1],
                edit_region,
            )
        )
    out_buf = BytesIO()
    heat.save(out_buf, format="PNG", optimize=True)
    return out_buf.getvalue()


def _inpaint_metrics(
    scene_bytes: bytes, mask_rgba_bytes: bytes, out_bytes: bytes
) -> dict[str, Any]:
    with Image.open(BytesIO(scene_bytes)) as scene_img, Image.open(
        BytesIO(mask_rgba_bytes)
    ) as mask_img, Image.open(BytesIO(out_bytes)) as out_img:
        mask_rgba = mask_img.convert("RGBA")
        W, H = mask_rgba.size
        scene = scene_img.convert("RGB")
        if scene.size != (W, H):
            scene = scene.resize((W, H), Image.LANCZOS)
        out = out_img.convert("RGB")
        if out.size != (W, H):
            out = out.resize((W, H), Image.LANCZOS)

    preserve_region, edit_region = _split_regions(mask_rgba)
    diff_scene_vs_out = _diff_magnitude(scene, out)

    preserve_score = _region_mean(diff_scene_vs_out, preserve_region)
    edit_score = _region_mean(diff_scene_vs_out, edit_region)
    ratio_score = edit_score / max(preserve_score, 1e-3)
    heatmap_bytes = _make_diff_heatmap(diff_scene_vs_out)

    return {
        "preserve_score": preserve_score,
        "edit_score": edit_score,
        "ratio_score": ratio_score,
        "heatmap_bytes": heatmap_bytes,
    }


def _outpaint_metrics(
    canvas_bytes: bytes, mask_rgba_bytes: bytes, out_bytes: bytes
) -> dict[str, Any]:
    with Image.open(BytesIO(canvas_bytes)) as canvas_img, Image.open(
        BytesIO(mask_rgba_bytes)
    ) as mask_img, Image.open(BytesIO(out_bytes)) as out_img:
        canvas_rgba = canvas_img.convert("RGBA")
        W, H = canvas_rgba.size
        mask_rgba = mask_img.convert("RGBA")
        if mask_rgba.size != (W, H):
            mask_rgba = mask_rgba.resize((W, H), Image.NEAREST)
        out = out_img.convert("RGB")
        if out.size != (W, H):
            out = out.resize((W, H), Image.LANCZOS)

    preserve_region, edit_region = _split_regions(mask_rgba)

    # For preserve_score, compare RGB of canvas vs RGB of output in the
    # preserve region. The canvas's preserve region is the original photo
    # so this directly measures "did the model touch the original?".
    canvas_rgb = canvas_rgba.convert("RGB")
    diff_canvas_vs_out = _diff_magnitude(canvas_rgb, out)
    preserve_score = _region_mean(diff_canvas_vs_out, preserve_region)

    # Black-void detection: count pixels in the edit region where all
    # three channels are < 10. The 10-threshold matches the design doc
    # and is generous enough to catch dithered upstream blacks.
    r, g, b = out.split()
    out_max = ImageChops.lighter(ImageChops.lighter(r, g), b)
    void_pixels_L = out_max.point(lambda v: 255 if v < 10 else 0)
    void_in_edit = ImageChops.multiply(void_pixels_L, edit_region)
    void_count = _region_pixel_count(void_in_edit)
    edit_count = max(_region_pixel_count(edit_region), 1)
    black_void_pct = 100.0 * void_count / edit_count

    # Edge density in the edit region — Sobel via PIL's FIND_EDGES filter.
    out_L = out.convert("L")
    edges = out_L.filter(ImageFilter.FIND_EDGES)
    edge_pixels_L = edges.point(lambda v: 255 if v > 20 else 0)
    edge_in_edit = ImageChops.multiply(edge_pixels_L, edit_region)
    edge_count = _region_pixel_count(edge_in_edit)
    extension_edges_pct = 100.0 * edge_count / edit_count

    heatmap_bytes = _make_diff_heatmap(diff_canvas_vs_out, edit_region=edit_region)

    return {
        "preserve_score": preserve_score,
        "black_void_pct": black_void_pct,
        "extension_edges_pct": extension_edges_pct,
        "heatmap_bytes": heatmap_bytes,
    }


def _judge_mask_inpaint(case_id: str):
    """Factory: bind ``case_id`` so we know which fixture to compare to."""

    ctx = _MASK_CASE_CTX[case_id]
    target = ctx["target"]

    def _judge(
        request: NormalizedRequest, response: NormalizedResponse
    ) -> tuple[bool, list[Verdict]]:
        verdicts: list[Verdict] = []
        if response.image_count <= 0:
            return False, [Verdict(False, "未返回任何图片")]

        info = probe_image(response.images[0])
        verdicts.append(Verdict(True, f"返回图 {info['width']}×{info['height']}"))

        try:
            from app.resources.test_assets import (
                load_mask_inpaint,
                load_mask_scene,
            )

            scene_bytes = load_mask_scene().data
            mask_bytes = load_mask_inpaint(target, "native").data
            m = _inpaint_metrics(scene_bytes, mask_bytes, response.images[0].data)
        except Exception as exc:  # pragma: no cover — defensive
            return False, [Verdict(False, f"指标计算失败: {exc!r}")]

        pres_ok = m["preserve_score"] < 0.05
        edit_ok = m["edit_score"] > 0.08
        ratio_ok = m["ratio_score"] > 3.0
        verdicts.append(
            Verdict(
                pres_ok,
                f"preserve_score = {m['preserve_score']:.3f} (< 0.05 → 保留区未被波及)",
            )
        )
        verdicts.append(
            Verdict(
                edit_ok,
                f"edit_score = {m['edit_score']:.3f} (> 0.08 → mask 区确实被改)",
            )
        )
        verdicts.append(
            Verdict(
                ratio_ok,
                f"ratio_score = {m['ratio_score']:.1f} (> 3.0 → 改和不改有显著区分)",
            )
        )
        ok = pres_ok and edit_ok and ratio_ok

        _MASK_JUDGE_LAST_RUN[case_id] = {
            "metrics": {
                "preserve_score": m["preserve_score"],
                "edit_score": m["edit_score"],
                "ratio_score": m["ratio_score"],
                "preserve_threshold": 0.05,
                "edit_threshold": 0.08,
                "ratio_threshold": 3.0,
                "preserve_ok": pres_ok,
                "edit_ok": edit_ok,
                "ratio_ok": ratio_ok,
            },
            "heatmap_bytes": m["heatmap_bytes"],
            "overlay_loader": ("inpaint", target),
        }
        return ok, verdicts

    return _judge


def _judge_mask_outpaint(case_id: str):
    """Factory: bind ``case_id`` so we know which canvas/mask to compare."""

    ctx = _MASK_CASE_CTX[case_id]
    scenario = ctx["scenario"]

    def _judge(
        request: NormalizedRequest, response: NormalizedResponse
    ) -> tuple[bool, list[Verdict]]:
        verdicts: list[Verdict] = []
        if response.image_count <= 0:
            return False, [Verdict(False, "未返回任何图片")]

        info = probe_image(response.images[0])
        verdicts.append(Verdict(True, f"返回图 {info['width']}×{info['height']}"))

        try:
            from app.resources.test_assets import load_mask_outpaint

            canvas_bytes = load_mask_outpaint(scenario, "canvas").data
            mask_bytes = load_mask_outpaint(scenario, "native").data
            m = _outpaint_metrics(canvas_bytes, mask_bytes, response.images[0].data)
        except Exception as exc:  # pragma: no cover — defensive
            return False, [Verdict(False, f"指标计算失败: {exc!r}")]

        pres_ok = m["preserve_score"] < 0.05
        void_ok = m["black_void_pct"] < 5.0
        edges_ok = m["extension_edges_pct"] > 1.0
        verdicts.append(
            Verdict(
                pres_ok,
                f"preserve_score = {m['preserve_score']:.3f} (< 0.05 → 原图区未被波及)",
            )
        )
        verdicts.append(
            Verdict(
                void_ok,
                f"black_void_pct = {m['black_void_pct']:.1f}% (< 5% → 扩展区无黑色 void)",
            )
        )
        verdicts.append(
            Verdict(
                edges_ok,
                f"extension_edges_pct = {m['extension_edges_pct']:.2f}% (> 1.0% → 扩展区有内容)",
            )
        )
        ok = pres_ok and void_ok and edges_ok

        _MASK_JUDGE_LAST_RUN[case_id] = {
            "metrics": {
                "preserve_score": m["preserve_score"],
                "black_void_pct": m["black_void_pct"],
                "extension_edges_pct": m["extension_edges_pct"],
                "preserve_threshold": 0.05,
                "black_void_threshold": 5.0,
                "extension_edges_threshold": 1.0,
                "preserve_ok": pres_ok,
                "black_void_ok": void_ok,
                "extension_edges_ok": edges_ok,
            },
            "heatmap_bytes": m["heatmap_bytes"],
            "overlay_loader": ("outpaint", scenario),
        }
        return ok, verdicts

    return _judge


# Side-channel for the runner to pick up diagnostics + metrics after the
# judge closure runs. Keyed by case_id; cleared once persisted. We keep
# it module-level (not in the closure) so the runner doesn't need to
# know which factory built the judge.
_MASK_JUDGE_LAST_RUN: dict[str, dict[str, Any]] = {}


def _judge_gemini_a1(
    request: NormalizedRequest, response: NormalizedResponse
) -> tuple[bool, list[Verdict]]:
    return _judge_basic_image(request, response)


def _judge_gemini_aspect(target: str, tol: float = 0.06):
    def _judge(
        request: NormalizedRequest, response: NormalizedResponse
    ) -> tuple[bool, list[Verdict]]:
        verdicts: list[Verdict] = []
        ok = response.image_count > 0
        if not ok:
            verdicts.append(Verdict(False, "未返回任何图片"))
            return ok, verdicts
        info = probe_image(response.images[0])
        match = aspect_close(info["width"], info["height"], target, tol=tol)
        verdicts.append(
            Verdict(
                match,
                f"返回图 {info['width']}×{info['height']} "
                f"(目标比例 {target}, 容差 {int(tol*100)}%)",
            )
        )
        if not match:
            ok = False
        return ok, verdicts

    return _judge


def _judge_gemini_size(
    label: str,
    *,
    min_long_edge: int | None = None,
    max_long_edge: int | None = None,
):
    """Build a judge that asserts ``max(w,h)`` lies in [min, max].

    Either bound is optional: 1K/2K/4K only need a lower bound (relays
    can render *bigger* than requested without it being a regression),
    while 512 only needs an upper bound (the spec says 512 maps to
    something around 512–768px). Catches the symmetric "relay ignored
    imageConfig" failure for both.
    """

    def _judge(
        request: NormalizedRequest, response: NormalizedResponse
    ) -> tuple[bool, list[Verdict]]:
        verdicts: list[Verdict] = []
        if response.image_count <= 0:
            verdicts.append(Verdict(False, "未返回任何图片"))
            return False, verdicts

        info = probe_image(response.images[0])
        long_edge = max(info["width"], info["height"])

        bound_parts: list[str] = []
        match = True
        if min_long_edge is not None:
            bound_parts.append(f"≥ {min_long_edge}px")
            if long_edge < min_long_edge:
                match = False
        if max_long_edge is not None:
            bound_parts.append(f"≤ {max_long_edge}px")
            if long_edge > max_long_edge:
                match = False
        bound_text = " / ".join(bound_parts) if bound_parts else "无界"

        verdicts.append(
            Verdict(
                match,
                f"返回图最长边 {long_edge}px (image_size={label} 期望 {bound_text}); "
                + ("尺寸合规" if match else "中继可能未透传 imageConfig"),
            )
        )
        return match, verdicts

    return _judge


def _judge_gemini_grounding(
    request: NormalizedRequest, response: NormalizedResponse
) -> tuple[bool, list[Verdict]]:
    verdicts: list[Verdict] = []
    ok = response.image_count > 0
    if not ok:
        verdicts.append(Verdict(False, "未返回任何图片"))
        return ok, verdicts
    grounding = response.metadata.get("grounding_metadata") if response.metadata else None
    has_grounding = bool(grounding)
    verdicts.append(
        Verdict(
            has_grounding,
            "groundingMetadata: 非空(grounding 工具被调用)"
            if has_grounding
            else "groundingMetadata: 缺失(中转可能丢弃了 grounding · 仅供参考)",
        )
    )
    # SEMI 用例 — 不在 auto-judge 这里一票否决。即使 grounding 缺失,
    # 也要让管理员看到"待人工"chip 和上面的红色 ✗ 提示,自己拍板。
    # 早先 Copilot 的 review 让我们在这里 ok=False,但那与 SEMI 的设计
    # 意图(参考算法,人工拍板)冲突,这里反过来。
    return ok, verdicts


def _judge_gemini_thoughts(
    request: NormalizedRequest, response: NormalizedResponse
) -> tuple[bool, list[Verdict]]:
    verdicts: list[Verdict] = []
    ok = response.image_count > 0
    if not ok:
        verdicts.append(Verdict(False, "未返回任何图片"))
        return ok, verdicts
    sigs = response.metadata.get("thought_signatures") if response.metadata else None
    has = bool(sigs)
    verdicts.append(
        Verdict(
            has,
            f"thought_signatures 数组长度 {len(sigs) if sigs else 0} (include_thoughts=True 应非空)",
        )
    )
    if not has:
        ok = False
    return ok, verdicts


def _judge_d_error(
    request: NormalizedRequest,
    error: StandardError | None,
    case_id: str,
    adapter_type: str,
) -> tuple[bool, list[Verdict]]:
    verdicts: list[Verdict] = []
    if error is None:
        verdicts.append(
            Verdict(False, "期望抛出 StandardError 但调用成功了")
        )
        return False, verdicts
    expected = expected_error_kinds(case_id, adapter_type)
    got = error.kind.value
    ok = got in expected
    verdicts.append(
        Verdict(
            ok,
            f"error_kind={got}, field={error.field}; "
            f"期望 ∈ {sorted(expected)}",
        )
    )
    if error.message:
        verdicts.append(Verdict(True, f"message: {error.message[:120]}"))
    return ok, verdicts


_BASIC_JUDGES: dict[str, Callable[..., tuple[bool, list[Verdict]]]] = {
    "openai_a1": _judge_openai_a1,
    "openai_b1": _judge_openai_b1,
    "openai_b2": _judge_openai_b2,
    "openai_b3": _judge_openai_b3,
    "openai_c3": _judge_openai_c3,
    "openai_basic_image": _judge_basic_image,
    "gemini_a1": _judge_gemini_a1,
    "gemini_aspect_16_9": _judge_gemini_aspect("16:9"),
    "gemini_aspect_9_16": _judge_gemini_aspect("9:16"),
    "gemini_aspect_1_4": _judge_gemini_aspect("1:4"),
    "gemini_aspect_8_1": _judge_gemini_aspect("8:1"),
    "gemini_size_2k": _judge_gemini_size("2K", min_long_edge=1900),
    "gemini_size_4k": _judge_gemini_size("4K", min_long_edge=3600),
    "gemini_size_512": _judge_gemini_size("512", max_long_edge=768),
    "gemini_grounding": _judge_gemini_grounding,
    "gemini_thoughts": _judge_gemini_thoughts,
    "gemini_basic_image": _judge_basic_image,
}


# ---------------------------------------------------------------------------
# Image persistence
# ---------------------------------------------------------------------------


def _ext_for_mime(mime: str) -> str:
    if mime == "image/jpeg":
        return "jpg"
    if mime == "image/webp":
        return "webp"
    if mime == "image/gif":
        return "gif"
    return "png"


def _store_images(
    run: _RunState, case_id: str, images: list[NormalizedImage]
) -> list[_StoredImage]:
    """Write each image to the run dir, return stored metadata."""
    stored: list[_StoredImage] = []
    run_dir = _run_dir(run.provider_id, run.run_id)
    for idx, img in enumerate(images):
        ext = _ext_for_mime(img.mime)
        name = f"{case_id}_{idx}.{ext}"
        path = run_dir / name
        try:
            path.write_bytes(img.data)
        except OSError as exc:
            logger.warning("test_suite: cannot persist %s: %s", path, exc)
            continue
        info = probe_image(img)
        stored.append(
            _StoredImage(
                case_id=case_id,
                idx=idx,
                mime=img.mime,
                width=info["width"],
                height=info["height"],
                byte_size=info["byte_size"],
                file_path=path,
            )
        )
    return stored


def _store_input_refs(
    run: _RunState, case_id: str, request: NormalizedRequest
) -> list[dict[str, Any]]:
    """Persist input references / mask so the UI can render thumbnails.

    Without this the admin only sees the OUTPUT image — they have no
    visual baseline to judge "was the logo preserved?" / "is the mask
    edge respected?". Per the design doc (manual_prompt rewrite §5.1),
    SEMI/MANUAL cases need to surface the inputs the upstream actually
    received. We reuse the run dir + signed-URL pipeline that already
    serves output images, prefixing the names with ``IN_`` so they
    can't collide with output filenames.

    Returns a list of dicts ready for the ``case_start`` SSE event:
    ``[{kind, label, name, mime, byte_size, bytes_url}, ...]``
    """
    out: list[dict[str, Any]] = []
    run_dir = _run_dir(run.provider_id, run.run_id)

    def _persist(label: str, kind: str, idx: int, raw_b64: str, mime: str) -> None:
        try:
            data = base64.b64decode(raw_b64, validate=False)
        except Exception:
            return
        ext = _ext_for_mime(mime)
        name = f"IN_{case_id}_{kind}_{idx}.{ext}"
        path = run_dir / name
        try:
            path.write_bytes(data)
        except OSError as exc:
            logger.warning("test_suite: cannot persist input %s: %s", path, exc)
            return
        out.append(
            {
                "kind": kind,
                "label": label,
                "name": name,
                "mime": mime,
                "byte_size": len(data),
                "bytes_url": _image_url(run.provider_id, run.run_id, name),
            }
        )

    refs = sorted(request.references or [], key=lambda r: r.order)
    for r in refs:
        _persist(f"参考图 {r.order}", "ref", r.order, r.data_b64, r.mime)
    if request.mask is not None:
        _persist("mask", "mask", 0, request.mask.data_b64, request.mask.mime)

    return out


def _persist_mask_diagnostics(
    run: _RunState,
    case_id: str,
    heatmap_bytes: bytes,
    overlay_loader: tuple[str, str] | None,
) -> list[_StoredDiagnostic]:
    """Persist the diff heatmap + the human-review overlay for a mask case.

    ``overlay_loader`` is ``(kind, target_or_scenario)``; we look up the
    overlay PNG via the existing asset loaders rather than copying yet
    another piece of fixture data through SSE.
    """

    out: list[_StoredDiagnostic] = []
    run_dir = _run_dir(run.provider_id, run.run_id)

    def _save(name: str, label: str, kind: str, data: bytes, mime: str) -> None:
        path = run_dir / name
        try:
            path.write_bytes(data)
        except OSError as exc:
            logger.warning("test_suite: cannot persist diagnostic %s: %s", path, exc)
            return
        out.append(
            _StoredDiagnostic(
                name=name,
                label=label,
                kind=kind,
                mime=mime,
                byte_size=len(data),
                file_path=path,
            )
        )

    _save(
        f"DX_{case_id}_diff_heatmap.png",
        "差分热力图",
        "heatmap",
        heatmap_bytes,
        "image/png",
    )

    if overlay_loader is not None:
        try:
            kind, key = overlay_loader
            if kind == "inpaint":
                from app.resources.test_assets import load_mask_inpaint

                overlay = load_mask_inpaint(key, "overlay")
            else:
                from app.resources.test_assets import load_mask_outpaint

                overlay = load_mask_outpaint(key, "overlay")
            _save(
                f"DX_{case_id}_mask_overlay.png",
                "Mask 叠加 (人工审核用)",
                "overlay",
                overlay.data,
                overlay.mime,
            )
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("test_suite: overlay load failed for %s: %s", case_id, exc)

    return out


# ---------------------------------------------------------------------------
# SSE event helpers
# ---------------------------------------------------------------------------


def _sse(event: str, data: dict[str, Any]) -> bytes:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


# ---------------------------------------------------------------------------
# Runner entrypoint
# ---------------------------------------------------------------------------


@dataclass
class RunRequest:
    provider_id: str
    provider: ProviderConfig
    adapter: BaseAdapter
    model_id: str
    suites: list[str]
    case_ids: list[str] | None
    capabilities: dict[str, Any]
    actor_id: str
    dry_run: bool = False


def _make_run_id() -> str:
    return "tsuite_" + secrets.token_hex(8)


def _select_cases(req: RunRequest) -> tuple[list[TestCase], list[str]]:
    """Pick which TestCases to run for ``req``.

    Filters by:
      * capability gating
      * suites whitelist
      * explicit case_ids whitelist (when provided)
      * dry_run → only suite D (zero cost)
    """
    matrix = get_matrix(req.model_id)
    if not matrix:
        return [], []

    matrix, dropped = _filter_by_capabilities(matrix, req.capabilities)

    suites = set(req.suites or ["A"])
    if req.dry_run:
        suites = {"D"}

    selected: list[TestCase] = []
    for case in matrix:
        if case.suite not in suites:
            continue
        if req.case_ids and case.case_id not in req.case_ids:
            continue
        selected.append(case)
    return selected, dropped


def _enforce_priority(cases: list[TestCase]) -> list[TestCase]:
    """Sort A→B→C→D so A failures can short-circuit subsequent suites."""
    order = {"A": 0, "B": 1, "C": 2, "D": 3}
    return sorted(cases, key=lambda c: (order.get(c.suite, 9), c.case_id))


async def stream_run(req: RunRequest) -> AsyncIterator[bytes]:
    """Async generator yielding SSE bytes for one provider test-suite run.

    The generator is consumed by FastAPI's ``StreamingResponse``. It
    yields events in this order::

        run_start  → {run_id, provider_id, model_id, total_cases, total_cost_planned}
        case_start → {case_id, suite, title, params, judge_level, cost_image}
        case_image → {case_id, idx, name, mime, width, height, byte_size, bytes_url}
        case_result → {case_id, ok, auto_verdict, manual_required, manual_prompt,
                       error_kind, error_message, latency_ms, cost_image}
        run_done   → {run_id, totals, verdict, skipped_by_capability, cost_used}
    """
    run_id = _make_run_id()
    run = _RunState(
        run_id=run_id,
        provider_id=req.provider_id,
        model_id=req.model_id,
        started_at=time.time(),
        actor_id=req.actor_id,
    )
    _runs[run_id] = run

    cases, dropped = _select_cases(req)
    cases = _enforce_priority(cases)
    run.skipped_by_capability = dropped

    total_cost = sum(1 for c in cases if c.cost_image)
    yield _sse(
        "run_start",
        {
            "run_id": run_id,
            "provider_id": req.provider_id,
            "model_id": req.model_id,
            "total_cases": len(cases),
            "total_cost_planned": total_cost,
            "skipped_by_capability": dropped,
            "dry_run": req.dry_run,
        },
    )

    a_failed = False
    for case in cases:
        if a_failed and case.suite != "A":
            # A 失败时跳过其余套件
            stub = _CaseResult(
                case_id=case.case_id,
                suite=case.suite,
                title=case.title,
                judge_level=case.judge_level,
                cost_image=False,
                ok=False,
                auto_verdict=[{"pass": False, "text": "A 套件失败,跳过此用例"}],
                manual_required=False,
                manual_prompt=None,
                manual_verdict="skip",
                error_kind="SKIPPED_AFTER_A_FAILURE",
                error_message="健康检查失败,后续套件已跳过",
                latency_ms=None,
                images=[],
            )
            run.cases[case.case_id] = stub
            yield _sse("case_start", _case_start_payload(case, request_params={}))
            yield _sse("case_result", _case_result_payload(stub, run_id, req.provider_id))
            continue

        try:
            request = case.request_factory()
        except Exception as exc:  # pragma: no cover — factory should not throw
            logger.exception("case %s: request_factory failed", case.case_id)
            stub = _CaseResult(
                case_id=case.case_id,
                suite=case.suite,
                title=case.title,
                judge_level=case.judge_level,
                cost_image=False,
                ok=False,
                auto_verdict=[
                    {"pass": False, "text": f"用例构造失败: {type(exc).__name__}: {exc}"}
                ],
                manual_required=False,
                manual_prompt=None,
                manual_verdict=None,
                error_kind="OTHER",
                error_message=str(exc),
                latency_ms=None,
                images=[],
            )
            run.cases[case.case_id] = stub
            yield _sse("case_start", _case_start_payload(case, request_params={}))
            yield _sse("case_result", _case_result_payload(stub, run_id, req.provider_id))
            if case.suite == "A":
                a_failed = True
            continue

        params_dump = _safe_dump_request(request)
        # Persist input references / mask so the UI can show them
        # alongside the manual_prompt block. Cheap (5KB-ish per ref)
        # and only triggers for cases that actually have inputs.
        case_inputs: list[dict[str, Any]] = []
        if request.references or request.mask is not None:
            case_inputs = _store_input_refs(run, case.case_id, request)
        yield _sse(
            "case_start",
            _case_start_payload(case, params_dump, inputs=case_inputs),
        )

        started = time.monotonic()
        try:
            response = await req.adapter.generate(req.provider, request)
            elapsed = (time.monotonic() - started) * 1000
            error: StandardError | None = None
        except StandardError as exc:
            elapsed = (time.monotonic() - started) * 1000
            response = None
            error = exc
        except Exception as exc:  # pragma: no cover — defensive
            elapsed = (time.monotonic() - started) * 1000
            response = None
            error = req.adapter.normalize_error(exc) if hasattr(req.adapter, "normalize_error") else None
            if error is None:
                # Wrap as OTHER to avoid leaking raw exceptions
                from app.schemas.normalized import StandardErrorKind
                error = StandardError(StandardErrorKind.OTHER, str(exc))

        # Persist images first so we can stream them before result.
        stored_images: list[_StoredImage] = []
        if response is not None and response.image_count > 0:
            stored_images = _store_images(run, case.case_id, response.images)
            for s in stored_images:
                run.cost_used += 1 if case.cost_image else 0
                yield _sse(
                    "case_image",
                    {
                        "case_id": s.case_id,
                        "idx": s.idx,
                        "name": s.file_path.name,
                        "mime": s.mime,
                        "width": s.width,
                        "height": s.height,
                        "byte_size": s.byte_size,
                        "bytes_url": _image_url(req.provider_id, run_id, s.file_path.name),
                    },
                )
        elif case.cost_image:
            # We attempted an upstream call (whether or not it succeeded);
            # admins should still see the budget tick — except the runner
            # only counts the image quota, not the request quota. We
            # increment ``cost_used`` from the image loop above; here is
            # a no-op for clarity.
            pass

        # Grade the case.
        ok = False
        verdicts: list[Verdict] = []
        judge = _BASIC_JUDGES.get(case.judge_name)
        # Mask cases need a per-case_id binding to know which fixture
        # to compare against. We don't want the case rows to carry that
        # binding directly (so test_cases.py stays declarative), so the
        # runner builds the closure on the fly via the factory tables.
        if case.judge_name == "mask_inpaint" and case.case_id in _MASK_CASE_CTX:
            judge = _judge_mask_inpaint(case.case_id)
        elif case.judge_name == "mask_outpaint" and case.case_id in _MASK_CASE_CTX:
            judge = _judge_mask_outpaint(case.case_id)
        if case.expect_error:
            ok, verdicts = _judge_d_error(
                request, error, case.case_id, req.adapter.adapter_type
            )
        else:
            if response is None:
                # Adapter raised on a happy-path case → that's a failure.
                err_msg = error.message if error else "adapter raised"
                kind = error.kind.value if error else "OTHER"
                verdicts.append(
                    Verdict(False, f"上游/适配器失败: {kind}: {err_msg}")
                )
                ok = False
            elif judge is None:
                ok, verdicts = _judge_basic_image(request, response)
            else:
                ok, verdicts = judge(request, response)

        manual_required = case.judge_level in ("SEMI", "MANUAL") and ok
        if not ok:
            manual_required = False

        # Pick up any diagnostics + metrics the mask judge stashed for
        # this case. Persist them with a ``DX_`` prefix so the run-dir
        # listing stays human-scannable (IN_* inputs, DX_* diagnostics,
        # bare case_id outputs).
        diagnostics: list[_StoredDiagnostic] = []
        mask_metrics: dict[str, float] | None = None
        side = _MASK_JUDGE_LAST_RUN.pop(case.case_id, None)
        if side is not None:
            mask_metrics = side["metrics"]
            diagnostics = _persist_mask_diagnostics(
                run, case.case_id, side["heatmap_bytes"], side["overlay_loader"]
            )

        # SEMI/MANUAL cases default-pass-and-warn until admin clicks. For
        # AUTO cases the runner's verdict is final.
        result = _CaseResult(
            case_id=case.case_id,
            suite=case.suite,
            title=case.title,
            judge_level=case.judge_level,
            cost_image=case.cost_image,
            ok=ok,
            auto_verdict=[v.to_dict() for v in verdicts],
            manual_required=manual_required,
            manual_prompt=case.manual_prompt,
            manual_verdict=None,
            error_kind=error.kind.value if error else None,
            error_message=error.message if error else None,
            latency_ms=round(elapsed, 2),
            images=stored_images,
            diagnostics=diagnostics,
            mask_metrics=mask_metrics,
        )
        run.cases[case.case_id] = result
        yield _sse("case_result", _case_result_payload(result, run_id, req.provider_id))

        if case.suite == "A" and not ok:
            a_failed = True

    # Compute totals. Short-circuit "skipped after A failed" cases are
    # tagged with ``manual_verdict == "skip"`` regardless of judge_level,
    # so we check that first to keep them out of the failure bucket.
    totals = {"pass": 0, "warn": 0, "fail": 0, "skipped": 0}
    for r in run.cases.values():
        if r.manual_verdict == "skip":
            totals["skipped"] += 1
        elif r.judge_level == "AUTO":
            if r.ok:
                totals["pass"] += 1
            else:
                totals["fail"] += 1
        else:
            if not r.ok:
                totals["fail"] += 1
            elif r.manual_verdict == "pass":
                totals["pass"] += 1
            elif r.manual_verdict == "fail":
                totals["fail"] += 1
            else:
                # not yet decided — front-end will tally as "warn"
                totals["warn"] += 1

    verdict = _compute_verdict(run)
    run.verdict = verdict
    run.finished = True

    # Mask subverdict — only relevant when at least one M-case ran. Only
    # AUTO success counts (admin manual overrides don't roll up here; the
    # subverdict is a quantitative provider-capability signal).
    mask_results = {
        cid: r.ok
        for cid, r in run.cases.items()
        if cid in _MASK_CASE_CTX and r.manual_verdict != "skip"
    }
    mask_subverdict = compute_mask_subverdict(mask_results)

    yield _sse(
        "run_done",
        {
            "run_id": run_id,
            "provider_id": req.provider_id,
            "model_id": req.model_id,
            "totals": totals,
            "verdict": verdict,
            "skipped_by_capability": dropped,
            "cost_used": run.cost_used,
            "mask_subverdict": mask_subverdict,
        },
    )


def _safe_dump_request(req: NormalizedRequest) -> dict[str, Any]:
    """Strip references / mask binary blobs out before broadcasting."""
    data = req.model_dump(exclude_none=True)
    refs = data.pop("references", None) or []
    summarised: list[dict[str, Any]] = []
    for r in refs:
        summarised.append(
            {
                "order": r.get("order"),
                "mime": r.get("mime"),
                "filename": r.get("filename"),
                "data_b64_len": len(r.get("data_b64") or ""),
            }
        )
    if summarised:
        data["references"] = summarised
    if "mask" in data and isinstance(data["mask"], dict):
        data["mask"] = {
            "mime": data["mask"].get("mime"),
            "filename": data["mask"].get("filename"),
            "data_b64_len": len(data["mask"].get("data_b64") or ""),
        }
    return data


def _image_url(provider_id: str, run_id: str, name: str) -> str:
    """Same-origin signed URL the admin browser can load directly.

    Attaches an HMAC + expiry so the route can validate the request
    without a bearer token (``<img src>`` can't carry one). The TTL
    matches ``_RUN_TTL_SECONDS`` so URLs lapse with the run itself.
    """
    exp = int(time.time()) + _RUN_TTL_SECONDS
    sig = _sign_payload(provider_id, run_id, name, exp)
    return (
        f"/api/admin/providers/{provider_id}/test-suite/{run_id}/images/{name}"
        f"?sig={sig}&exp={exp}"
    )


def _case_start_payload(
    case: TestCase,
    request_params: dict[str, Any],
    inputs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "suite": case.suite,
        "title": case.title,
        "judge_level": case.judge_level,
        "cost_image": case.cost_image,
        "expect_error": case.expect_error,
        "manual_prompt": case.manual_prompt,
        "params": request_params,
        "inputs": inputs or [],
    }


def _case_result_payload(
    result: _CaseResult, run_id: str, provider_id: str
) -> dict[str, Any]:
    # Forward ``status`` explicitly so the frontend reducer doesn't have
    # to second-guess the difference between "failed test" and "skipped
    # because the A-suite already failed". Without this, short-circuit
    # cases (ok=False + manual_verdict="skip") would render as red
    # ``fail`` cards and double-count in the totals row.
    if result.manual_verdict == "skip":
        status = "skipped"
    elif result.ok and result.judge_level in ("SEMI", "MANUAL") and result.manual_required:
        status = "manual_pending"
    elif result.ok:
        status = "pass"
    else:
        status = "fail"
    return {
        "run_id": run_id,
        "provider_id": provider_id,
        "case_id": result.case_id,
        "suite": result.suite,
        "title": result.title,
        "judge_level": result.judge_level,
        "ok": result.ok,
        "status": status,
        "manual_verdict": result.manual_verdict,
        "auto_verdict": result.auto_verdict,
        "manual_required": result.manual_required,
        "manual_prompt": result.manual_prompt,
        "error_kind": result.error_kind,
        "error_message": result.error_message,
        "latency_ms": result.latency_ms,
        "cost_image": result.cost_image,
        "images": [
            {
                "idx": img.idx,
                "name": img.file_path.name,
                "mime": img.mime,
                "width": img.width,
                "height": img.height,
                "byte_size": img.byte_size,
                "bytes_url": _image_url(provider_id, run_id, img.file_path.name),
            }
            for img in result.images
        ],
        "diagnostics": [
            {
                "kind": d.kind,
                "label": d.label,
                "name": d.name,
                "mime": d.mime,
                "byte_size": d.byte_size,
                "bytes_url": _image_url(provider_id, run_id, d.name),
            }
            for d in result.diagnostics
        ],
        "mask_metrics": result.mask_metrics,
    }


def compute_mask_subverdict(case_results: dict[str, Any]) -> str | None:
    """5-tier mask subverdict for the C suite header.

    ``case_results`` is a mapping ``case_id → bool`` where ``True`` means
    "auto-judge passed". Missing case ids count as failures. Returns
    ``None`` when no mask case ran at all (e.g. only A/B suites were
    requested), so the UI can hide the badge.

    Tiers (matches the design doc §5.4):

    * PERFECT          — both native AND fallback succeeded on every
                         task (inpaint × outpaint × method).
    * STANDARD_ONLY    — native succeeded on both inpaint targets and
                         both outpaint scenarios; fallback may fail.
    * FALLBACK_ONLY    — fallback succeeded on both inpaint targets and
                         both outpaint scenarios; native may fail.
    * PARTIAL_UNUSABLE — at least one task (inpaint or outpaint) has at
                         least one working method, but the other task
                         has no working method.
    * UNUSABLE         — neither inpaint nor outpaint has a working
                         method.

    The "both targets must pass" rule defends against the model accident-
    ally guessing one target. ``M1 ∧ M3`` ⇒ native inpaint works;
    ``M2 ∧ M4`` ⇒ fallback inpaint works; analogously for outpaint.
    """

    def ran(case_id: str) -> bool:
        return case_id in case_results

    def passed(case_id: str) -> bool:
        return bool(case_results.get(case_id))

    if not any(ran(c) for c in MASK_CASE_IDS):
        return None

    native_inpaint = passed("M1") and passed("M3")
    fallback_inpaint = passed("M2") and passed("M4")
    native_outpaint = passed("M5") and passed("M7")
    fallback_outpaint = passed("M6") and passed("M8")

    inpaint_has_method = native_inpaint or fallback_inpaint
    outpaint_has_method = native_outpaint or fallback_outpaint

    if not inpaint_has_method and not outpaint_has_method:
        return "UNUSABLE"
    if not (inpaint_has_method and outpaint_has_method):
        return "PARTIAL_UNUSABLE"
    if (
        native_inpaint and fallback_inpaint
        and native_outpaint and fallback_outpaint
    ):
        return "PERFECT"
    if native_inpaint and native_outpaint:
        return "STANDARD_ONLY"
    if fallback_inpaint and fallback_outpaint:
        return "FALLBACK_ONLY"
    # Cross-method success (e.g. native inpaint + fallback outpaint) →
    # at least one method per task, but neither method is consistent.
    return "PARTIAL_UNUSABLE"


def _compute_verdict(run: _RunState) -> str:
    """PASS / WARN / FAIL based on the standing of every case in ``run``.

    ``D`` failures or any AUTO-level failure → FAIL.
    Any pending manual decision → WARN.
    Otherwise PASS.
    """
    has_fail = False
    has_pending_manual = False
    for r in run.cases.values():
        # Short-circuit skipped cases never feed into PASS/FAIL
        # accounting — they're an admin-visible "we didn't try this"
        # signal, distinct from a real failure.
        if r.manual_verdict == "skip":
            continue
        if r.judge_level == "AUTO":
            if not r.ok:
                has_fail = True
        else:
            if not r.ok:
                has_fail = True
            elif r.manual_verdict is None:
                has_pending_manual = True
            elif r.manual_verdict == "fail":
                has_fail = True
    if has_fail:
        return "FAIL"
    if has_pending_manual:
        return "WARN"
    return "PASS"


# ---------------------------------------------------------------------------
# Manual verdict
# ---------------------------------------------------------------------------


def apply_manual_verdict(run_id: str, case_id: str, verdict: str) -> _CaseResult | None:
    run = _runs.get(run_id)
    if run is None:
        return None
    case = run.cases.get(case_id)
    if case is None:
        return None
    if verdict not in ("pass", "fail", "skip"):
        return None
    case.manual_verdict = verdict
    if verdict == "fail":
        case.ok = False
    run.verdict = _compute_verdict(run)
    return case
