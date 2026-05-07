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

from PIL import Image

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


def _judge_openai_c3(
    request: NormalizedRequest, response: NormalizedResponse
) -> tuple[bool, list[Verdict]]:
    verdicts: list[Verdict] = []
    ok = response.image_count > 0
    if response.images:
        info = probe_image(response.images[0])
        verdicts.append(
            Verdict(
                info["width"] > 0,
                f"返回图 {info['width']}×{info['height']}",
            )
        )
        # 自动算左半相似度 (相对原 base) — 仅作参考。
        try:
            from app.resources.test_assets import load_edit_base

            base_asset = load_edit_base()
            with Image.open(BytesIO(base_asset.data)) as base_img, Image.open(
                BytesIO(response.images[0].data)
            ) as out_img:
                w = min(base_img.size[0] // 2, out_img.size[0] // 2)
                h = min(base_img.size[1], out_img.size[1])
                if w > 0 and h > 0:
                    base_left = base_img.crop((0, 0, w, h)).resize((128, 128)).convert("RGB")
                    out_left = out_img.crop((0, 0, w, h)).resize((128, 128)).convert("RGB")
                    diff_pixels = 0
                    for a, b in zip(base_left.getdata(), out_left.getdata()):
                        if abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2]) > 30:
                            diff_pixels += 1
                    ratio = 1 - diff_pixels / (128 * 128)
                    verdicts.append(
                        Verdict(
                            ratio >= 0.85,
                            f"左半区与原图像素相似度 ≈ {ratio:.0%} (建议 ≥85%)",
                        )
                    )
        except Exception as exc:
            verdicts.append(Verdict(True, f"相似度计算跳过: {exc!r}"))
    return ok, verdicts


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
        Verdict(has_grounding, f"groundingMetadata: {'非空' if has_grounding else '缺失'}")
    )
    # A relay that drops grounding-related fields should fail the case
    # outright, not coast on "image returned". Earlier this was a
    # warning-only signal and SEMI status masked the real failure.
    if not has_grounding:
        ok = False
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
        yield _sse("case_start", _case_start_payload(case, params_dump))

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


def _case_start_payload(case: TestCase, request_params: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "suite": case.suite,
        "title": case.title,
        "judge_level": case.judge_level,
        "cost_image": case.cost_image,
        "expect_error": case.expect_error,
        "params": request_params,
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
    }


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
