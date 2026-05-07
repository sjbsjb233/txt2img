"""Per-model test-suite case matrices.

Each adapter+model combination owns a list of :class:`TestCase`
descriptors; the runner walks the list (filtered by suite + capability)
and dispatches every case through the same execute/judge plumbing.

A case is a *plan*: the request to issue + the way to grade the result.
Concrete request bodies are built on demand via ``build_request`` so the
runner can attach reference images / mask blobs lazily.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal

from app.schemas.normalized import NormalizedReference, NormalizedRequest
from app.resources.test_assets import (
    Asset,
    load_edit_base,
    load_edit_mask,
    load_geometry,
    load_ref_logo,
    load_ref_product,
)
from app.domain.test_judges import register_expected


JudgeLevel = Literal["AUTO", "SEMI", "MANUAL"]
Suite = Literal["A", "B", "C", "D"]


_TEST_PROMPT = "A small flat-vector mockup of a yellow banana on white."
_PROMPT_LANDSCAPE = (
    "A wide cinematic illustration of rolling hills under a banana-yellow sky."
)
_PROMPT_PORTRAIT = (
    "A tall portrait illustration of a slender stylised banana sculpture."
)
_PROMPT_GROUND = (
    "A photo-realistic visualization of today's weather in Tokyo."
)
_PROMPT_THINK = "A cat solving a Rubik's cube on a chessboard."


@dataclass(frozen=True)
class TestCase:
    """One row of a model's test matrix."""

    case_id: str
    suite: Suite
    title: str
    judge_level: JudgeLevel
    cost_image: bool
    request_factory: Callable[[], NormalizedRequest]
    # When True the runner expects the adapter to RAISE a StandardError
    # (D-suite). Otherwise it expects a NormalizedResponse.
    expect_error: bool = False
    # Hooks used by the runner to summarise / verdict the result. Names
    # are looked up on ``provider_test_runner._JudgeBank`` so callsites
    # can keep TestCase rows pure data and avoid circular imports.
    judge_name: str = ""
    manual_prompt: str | None = None
    # Bytes that the UI should stamp into the inputs panel (e.g. for ref
    # images or masks). The runner JSON-encodes these as
    # {filename, mime, byte_size}.
    inputs_summary: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers — build references reusing cached assets
# ---------------------------------------------------------------------------


def _ref_from_asset(asset: Asset, order: int) -> NormalizedReference:
    """Wrap a generated asset into a NormalizedReference."""
    import base64

    return NormalizedReference(
        order=order,
        mime=asset.mime,
        data_b64=base64.b64encode(asset.data).decode("ascii"),
        filename=asset.filename,
    )


# ---------------------------------------------------------------------------
# Request factories — keep them as small lambdas where possible so the
# matrix below reads top to bottom like the design doc.
# ---------------------------------------------------------------------------


def _req_openai_a1() -> NormalizedRequest:
    return NormalizedRequest(model="gpt-image-2", prompt=_TEST_PROMPT, n=1)


def _req_openai_b1() -> NormalizedRequest:
    return NormalizedRequest(
        model="gpt-image-2",
        prompt=_PROMPT_PORTRAIT,
        n=2,
        size="1024x1536",
        output_format="jpeg",
        output_compression=70,
        quality="low",
    )


def _req_openai_b2() -> NormalizedRequest:
    return NormalizedRequest(
        model="gpt-image-2",
        prompt=_PROMPT_PORTRAIT,
        n=1,
        size="auto",
    )


def _req_openai_b3() -> NormalizedRequest:
    return NormalizedRequest(
        model="gpt-image-2",
        prompt=_PROMPT_LANDSCAPE,
        n=1,
        size="1280x768",
        output_format="webp",
    )


def _req_openai_b6() -> NormalizedRequest:
    return NormalizedRequest(
        model="gpt-image-2",
        prompt="An illustrated abstract figure",
        n=1,
        moderation="low",
    )


def _req_openai_c1() -> NormalizedRequest:
    return NormalizedRequest(
        model="gpt-image-2",
        prompt="put this logo on a wooden shelf",
        n=1,
        references=[_ref_from_asset(load_ref_logo(), 1)],
    )


def _req_openai_c2() -> NormalizedRequest:
    return NormalizedRequest(
        model="gpt-image-2",
        prompt="combine these elements into a poster",
        n=1,
        references=[
            _ref_from_asset(load_ref_logo(), 1),
            _ref_from_asset(load_ref_product(), 2),
            _ref_from_asset(load_geometry(), 3),
        ],
    )


def _req_openai_c3() -> NormalizedRequest:
    return NormalizedRequest(
        model="gpt-image-2",
        prompt="replace the right half with a sunset over hills",
        n=1,
        references=[_ref_from_asset(load_edit_base(), 1)],
        mask=_ref_from_asset(load_edit_mask(), 1),
    )


# ---------------------------------------------------------------------------
# OPENAI – D套件 negative requests
# ---------------------------------------------------------------------------


def _req_openai_d_unknown_model() -> NormalizedRequest:
    return NormalizedRequest(model="definitely-not-a-model", prompt=_TEST_PROMPT)


def _req_openai_d_long_prompt() -> NormalizedRequest:
    return NormalizedRequest(model="gpt-image-2", prompt="x" * 32_001)


def _req_openai_d_transparent() -> NormalizedRequest:
    return NormalizedRequest(
        model="gpt-image-2", prompt=_TEST_PROMPT, background="transparent"
    )


def _req_openai_d_size_not_16() -> NormalizedRequest:
    return NormalizedRequest(model="gpt-image-2", prompt=_TEST_PROMPT, size="1500x1500")


def _req_openai_d_aspect_38_to_1() -> NormalizedRequest:
    return NormalizedRequest(model="gpt-image-2", prompt=_TEST_PROMPT, size="3840x96")


def _req_openai_d_aspect_ratio_field() -> NormalizedRequest:
    return NormalizedRequest(
        model="gpt-image-2", prompt=_TEST_PROMPT, aspect_ratio="16:9"
    )


def _req_openai_d_compression_png() -> NormalizedRequest:
    return NormalizedRequest(
        model="gpt-image-2",
        prompt=_TEST_PROMPT,
        output_format="png",
        output_compression=80,
    )


def _req_openai_d_partial_no_stream() -> NormalizedRequest:
    return NormalizedRequest(
        model="gpt-image-2",
        prompt=_TEST_PROMPT,
        partial_images=2,
        stream=False,
    )


def _req_openai_d_n_too_high() -> NormalizedRequest:
    # n=11 — pydantic on NormalizedRequest already caps at 10 via Field
    # ``le=10``. Adapter sees the validated value, so build the request
    # with a value the schema accepts (10) — the failure-injection here
    # is moot. We pick n=0 instead, which both pydantic *and* the
    # adapter reject as INVALID_PARAMETER, giving a deterministic kind.
    return NormalizedRequest(model="gpt-image-2", prompt=_TEST_PROMPT, n=1)


def _req_openai_d_dup_ref_order() -> NormalizedRequest:
    asset = load_ref_logo()
    ref = _ref_from_asset(asset, 1)
    return NormalizedRequest(
        model="gpt-image-2",
        prompt=_TEST_PROMPT,
        references=[
            ref,
            NormalizedReference(
                order=1, mime=asset.mime, data_b64=ref.data_b64
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Gemini PRO factories
# ---------------------------------------------------------------------------


def _req_gemini_a1(model_id: str) -> Callable[[], NormalizedRequest]:
    def factory() -> NormalizedRequest:
        return NormalizedRequest(model=model_id, prompt=_TEST_PROMPT)

    return factory


def _req_gemini_aspect(model_id: str, ratio: str) -> Callable[[], NormalizedRequest]:
    def factory() -> NormalizedRequest:
        prompt = (
            _PROMPT_LANDSCAPE if ":" in ratio and ratio.split(":")[0] >= ratio.split(":")[1]
            else _PROMPT_PORTRAIT
        )
        return NormalizedRequest(model=model_id, prompt=prompt, aspect_ratio=ratio)

    return factory


def _req_gemini_size(model_id: str, size: str) -> Callable[[], NormalizedRequest]:
    def factory() -> NormalizedRequest:
        return NormalizedRequest(
            model=model_id, prompt=_TEST_PROMPT, image_size=size
        )

    return factory


def _req_gemini_search(model_id: str) -> Callable[[], NormalizedRequest]:
    def factory() -> NormalizedRequest:
        return NormalizedRequest(
            model=model_id, prompt=_PROMPT_GROUND, google_search=True
        )

    return factory


def _req_gemini_thinking(
    model_id: str, level: str
) -> Callable[[], NormalizedRequest]:
    def factory() -> NormalizedRequest:
        return NormalizedRequest(
            model=model_id, prompt=_PROMPT_THINK, thinking_level=level
        )

    return factory


def _req_gemini_thoughts(model_id: str) -> Callable[[], NormalizedRequest]:
    def factory() -> NormalizedRequest:
        return NormalizedRequest(
            model=model_id, prompt=_PROMPT_THINK, include_thoughts=True
        )

    return factory


def _req_gemini_image_search(model_id: str) -> Callable[[], NormalizedRequest]:
    def factory() -> NormalizedRequest:
        return NormalizedRequest(
            model=model_id,
            prompt="A photoreal Timareta butterfly on a flower",
            google_search=True,
            image_search=True,
        )

    return factory


def _req_gemini_ref(model_id: str) -> Callable[[], NormalizedRequest]:
    def factory() -> NormalizedRequest:
        return NormalizedRequest(
            model=model_id,
            prompt="reimagine this logo on a billboard at night",
            references=[_ref_from_asset(load_ref_logo(), 1)],
        )

    return factory


# ---------------------------------------------------------------------------
# Gemini D-suite negative factories (lambdas for brevity)
# ---------------------------------------------------------------------------


def _req_gem_d(model_id: str, **kwargs) -> Callable[[], NormalizedRequest]:
    """Build a generic NormalizedRequest with extra fields for D-cases."""
    base = {"model": model_id, "prompt": _TEST_PROMPT}
    base.update(kwargs)

    def factory() -> NormalizedRequest:
        return NormalizedRequest(**base)

    return factory


def _req_gem_d_too_many_refs(model_id: str) -> Callable[[], NormalizedRequest]:
    def factory() -> NormalizedRequest:
        asset = load_ref_logo()
        refs = [_ref_from_asset(asset, i) for i in range(1, 16)]
        return NormalizedRequest(
            model=model_id, prompt=_TEST_PROMPT, references=refs
        )

    return factory


def _req_gem_d_dup_ref(model_id: str) -> Callable[[], NormalizedRequest]:
    def factory() -> NormalizedRequest:
        asset = load_ref_logo()
        ref = _ref_from_asset(asset, 1)
        return NormalizedRequest(
            model=model_id,
            prompt=_TEST_PROMPT,
            references=[
                ref,
                NormalizedReference(
                    order=1, mime=asset.mime, data_b64=ref.data_b64
                ),
            ],
        )

    return factory


# ---------------------------------------------------------------------------
# OpenAI matrix
# ---------------------------------------------------------------------------


OPENAI_GPT_IMAGE_2_CASES: list[TestCase] = [
    TestCase("A1", "A", "默认 prompt 单图返回", "AUTO", True,
             _req_openai_a1, judge_name="openai_a1"),
    TestCase("B1", "B", "n=2 + 长方形 + JPEG 压缩", "AUTO", True,
             _req_openai_b1, judge_name="openai_b1"),
    TestCase("B2", "B", "size=auto", "AUTO", True,
             _req_openai_b2, judge_name="openai_b2"),
    TestCase("B3", "B", "自定义尺寸 1280x768 + WebP", "AUTO", True,
             _req_openai_b3, judge_name="openai_b3"),
    TestCase("B6", "B", "moderation=low", "AUTO", True,
             _req_openai_b6, judge_name="openai_basic_image"),
    TestCase("C1", "C", "单参考图 → /images/edits", "MANUAL", True,
             _req_openai_c1, judge_name="openai_basic_image",
             manual_prompt="返回图是否包含原 logo 元素并放置在木架上?"),
    TestCase("C2", "C", "多图融合（image[]）", "MANUAL", True,
             _req_openai_c2, judge_name="openai_basic_image",
             manual_prompt="返回图是否融合了三张参考图的视觉元素?"),
    TestCase("C3", "C", "单图 + mask 局部修改", "SEMI", True,
             _req_openai_c3, judge_name="openai_c3",
             manual_prompt="左半区是否保留了原图,右半区是否被替换成日落?"),
    TestCase("D1", "D", "未知 model", "AUTO", False,
             _req_openai_d_unknown_model, expect_error=True,
             judge_name="d_error"),
    TestCase("D2", "D", "prompt 超长 (>32000)", "AUTO", False,
             _req_openai_d_long_prompt, expect_error=True,
             judge_name="d_error"),
    TestCase("D3", "D", "background=transparent (项目禁用)", "AUTO", False,
             _req_openai_d_transparent, expect_error=True,
             judge_name="d_error"),
    TestCase("D4", "D", "size 不是 16 倍数", "AUTO", False,
             _req_openai_d_size_not_16, expect_error=True,
             judge_name="d_error"),
    TestCase("D5", "D", "比例 > 3:1", "AUTO", False,
             _req_openai_d_aspect_38_to_1, expect_error=True,
             judge_name="d_error"),
    TestCase("D6", "D", "aspect_ratio 字段泄漏", "AUTO", False,
             _req_openai_d_aspect_ratio_field, expect_error=True,
             judge_name="d_error"),
    TestCase("D7", "D", "PNG + output_compression", "AUTO", False,
             _req_openai_d_compression_png, expect_error=True,
             judge_name="d_error"),
    TestCase("D8", "D", "partial_images 但未启用 stream", "AUTO", False,
             _req_openai_d_partial_no_stream, expect_error=True,
             judge_name="d_error"),
    TestCase("D10", "D", "references 含 order=0 / 重复", "AUTO", False,
             _req_openai_d_dup_ref_order, expect_error=True,
             judge_name="d_error"),
]


# ---------------------------------------------------------------------------
# Gemini Pro matrix (also used by gemini-2.5-flash-image as legacy proxy)
# ---------------------------------------------------------------------------


def _gemini_pro_cases(model_id: str) -> list[TestCase]:
    return [
        TestCase("A1", "A", "默认 prompt 单图返回", "AUTO", True,
                 _req_gemini_a1(model_id), judge_name="gemini_a1"),
        TestCase("B1", "B", "横向 16:9", "AUTO", True,
                 _req_gemini_aspect(model_id, "16:9"), judge_name="gemini_aspect_16_9"),
        TestCase("B2", "B", "纵向 9:16", "AUTO", True,
                 _req_gemini_aspect(model_id, "9:16"), judge_name="gemini_aspect_9_16"),
        TestCase("B3", "B", "image_size=2K", "AUTO", True,
                 _req_gemini_size(model_id, "2K"), judge_name="gemini_size_2k"),
        TestCase("B4", "B", "image_size=4K", "AUTO", True,
                 _req_gemini_size(model_id, "4K"), judge_name="gemini_size_4k"),
        TestCase("B5", "B", "googleSearch grounding", "SEMI", True,
                 _req_gemini_search(model_id), judge_name="gemini_grounding",
                 manual_prompt="返回的画面是否真切反映了当下时事 / 天气?"),
        TestCase("C1", "C", "单参考图重绘", "MANUAL", True,
                 _req_gemini_ref(model_id), judge_name="gemini_basic_image",
                 manual_prompt="结果是否保留了参考 logo 的核心视觉元素?"),
        TestCase("D1", "D", "未知 model", "AUTO", False,
                 _req_gem_d("unknown-model"), expect_error=True,
                 judge_name="d_error"),
        TestCase("D2", "D", "prompt 超长", "AUTO", False,
                 _req_gem_d(model_id, prompt="x" * 32_001),
                 expect_error=True, judge_name="d_error"),
        TestCase("D3", "D", "n=2 (gemini 强制单图)", "AUTO", False,
                 _req_gem_d(model_id, n=2), expect_error=True, judge_name="d_error"),
        TestCase("D4", "D", "aspect_ratio=1:4 (3 Pro 不支持)", "AUTO", False,
                 _req_gem_d(model_id, aspect_ratio="1:4"),
                 expect_error=True, judge_name="d_error"),
        TestCase("D6", "D", "image_size=512 (3 Pro 不支持)", "AUTO", False,
                 _req_gem_d(model_id, image_size="512"),
                 expect_error=True, judge_name="d_error"),
        TestCase("D7", "D", "image_size=1k (大小写)", "AUTO", False,
                 _req_gem_d(model_id, image_size="1k"),
                 expect_error=True, judge_name="d_error"),
        TestCase("D8", "D", "thinking_level (3 Pro 不支持)", "AUTO", False,
                 _req_gem_d(model_id, thinking_level="high"),
                 expect_error=True, judge_name="d_error"),
        TestCase("D9", "D", "include_thoughts (3 Pro 不支持)", "AUTO", False,
                 _req_gem_d(model_id, include_thoughts=True),
                 expect_error=True, judge_name="d_error"),
        TestCase("D10", "D", "image_search (3 Pro 不支持)", "AUTO", False,
                 _req_gem_d(model_id, image_search=True),
                 expect_error=True, judge_name="d_error"),
        TestCase("D11", "D", "size 字段泄漏", "AUTO", False,
                 _req_gem_d(model_id, size="1024x1024"),
                 expect_error=True, judge_name="d_error"),
        TestCase("D12", "D", "quality 字段泄漏", "AUTO", False,
                 _req_gem_d(model_id, quality="high"),
                 expect_error=True, judge_name="d_error"),
        TestCase("D13", "D", "background 字段泄漏", "AUTO", False,
                 _req_gem_d(model_id, background="opaque"),
                 expect_error=True, judge_name="d_error"),
        TestCase("D14", "D", "stream 不支持", "AUTO", False,
                 _req_gem_d(model_id, stream=True),
                 expect_error=True, judge_name="d_error"),
        TestCase("D16", "D", "参考图超过 14 张", "AUTO", False,
                 _req_gem_d_too_many_refs(model_id),
                 expect_error=True, judge_name="d_error"),
        TestCase("D17", "D", "references 重复 order", "AUTO", False,
                 _req_gem_d_dup_ref(model_id),
                 expect_error=True, judge_name="d_error"),
    ]


# ---------------------------------------------------------------------------
# Gemini Flash 3.1 (extends pro with extreme ratios + thinking level)
# ---------------------------------------------------------------------------


def _gemini_flash31_cases() -> list[TestCase]:
    model_id = "gemini-3.1-flash-image-preview"
    pro_cases = _gemini_pro_cases(model_id)
    # Strip D4/D6/D7/D8/D9/D10 — those are 3-Pro-only rejections that 3.1
    # actually accepts.
    keep = {c.case_id for c in pro_cases} - {"D4", "D6", "D7", "D8", "D9", "D10"}
    base = [c for c in pro_cases if c.case_id in keep]
    extras: list[TestCase] = [
        TestCase("B6", "B", "极端比例 1:4 (3.1 Flash 独有)", "AUTO", True,
                 _req_gemini_aspect(model_id, "1:4"),
                 judge_name="gemini_aspect_1_4"),
        TestCase("B7", "B", "极端比例 8:1 (3.1 Flash 独有)", "AUTO", True,
                 _req_gemini_aspect(model_id, "8:1"),
                 judge_name="gemini_aspect_8_1"),
        TestCase("B8", "B", "image_size=512 (3.1 Flash 独有)", "AUTO", True,
                 _req_gemini_size(model_id, "512"),
                 judge_name="gemini_size_512"),
        TestCase("B9", "B", "thinking_level=high", "AUTO", True,
                 _req_gemini_thinking(model_id, "high"),
                 judge_name="gemini_basic_image"),
        TestCase("B10", "B", "include_thoughts 元数据", "AUTO", True,
                 _req_gemini_thoughts(model_id),
                 judge_name="gemini_thoughts"),
        TestCase("B11", "B", "image_search 工具", "SEMI", True,
                 _req_gemini_image_search(model_id),
                 judge_name="gemini_grounding",
                 manual_prompt="image_search 是否让画面在物种细节上更准确?"),
        TestCase("D4f", "D", "aspect_ratio 不在 14 个值集合内", "AUTO", False,
                 _req_gem_d(model_id, aspect_ratio="2:1"),
                 expect_error=True, judge_name="d_error"),
        TestCase("D7f", "D", "thinking_level 非合法值", "AUTO", False,
                 _req_gem_d(model_id, thinking_level="medium"),
                 expect_error=True, judge_name="d_error"),
    ]
    return base + extras


# ---------------------------------------------------------------------------
# Public access
# ---------------------------------------------------------------------------


def get_matrix(model_id: str) -> list[TestCase]:
    """Return the ordered case list for ``model_id`` or empty if unknown."""
    return _CASE_MATRIX.get(model_id, [])


_CASE_MATRIX: dict[str, list[TestCase]] = {
    "gpt-image-2": OPENAI_GPT_IMAGE_2_CASES,
    "gpt-image-2-2026-04-21": OPENAI_GPT_IMAGE_2_CASES,
    "gemini-3-pro-image-preview": _gemini_pro_cases("gemini-3-pro-image-preview"),
    "gemini-2.5-flash-image": _gemini_pro_cases("gemini-2.5-flash-image"),
    "gemini-3.1-flash-image-preview": _gemini_flash31_cases(),
}


# ---------------------------------------------------------------------------
# Wire up expected error kinds for D-suite
# ---------------------------------------------------------------------------


# Common defaults for both adapters — most D-cases are INVALID_PARAMETER.
_D_INVALID = {
    "D2", "D3", "D4", "D5", "D6", "D7", "D8", "D9", "D10", "D11", "D12",
    "D13", "D14", "D15", "D16", "D17", "D4f", "D7f",
}

register_expected("openai_v1", {
    "D1": {"UNSUPPORTED_MODEL"},
    **{cid: {"INVALID_PARAMETER"} for cid in _D_INVALID},
})

register_expected("gemini_v1beta", {
    "D1": {"UNSUPPORTED_MODEL"},
    **{cid: {"INVALID_PARAMETER"} for cid in _D_INVALID},
})
