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
    """Pick a landscape vs portrait prompt based on the requested ratio.

    The earlier implementation compared the two halves as strings, so
    e.g. ``"9:16"`` resolved as landscape because lexicographic
    ``"9" >= "16"`` is True. Parsed ints get the right answer.
    """

    def factory() -> NormalizedRequest:
        is_landscape = True
        if ":" in ratio:
            try:
                tw_str, th_str = ratio.split(":", 1)
                is_landscape = int(tw_str) >= int(th_str)
            except (TypeError, ValueError):
                is_landscape = True
        prompt = _PROMPT_LANDSCAPE if is_landscape else _PROMPT_PORTRAIT
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
             manual_prompt=(
                 "📋 此用例的入参:\n"
                 "  • 参考图 1 张:一个深色简笔 logo (纯图形,无文字)\n"
                 "  • prompt:\"put this logo on a wooden shelf\"\n"
                 "\n"
                 "✅ 同时满足 → 点 通过:\n"
                 "  ① 输出图里能识别出参考 logo 的主要轮廓/形状(允许配色、光影变化)\n"
                 "  ② 画面里有明显的木质架子/木板作为承载体\n"
                 "\n"
                 "❌ 任一 → 点 不通过:\n"
                 "  • 输出图里完全没有 logo 元素,只有木架\n"
                 "  • 输出图里只有 logo,没有任何木质架子/木板\n"
                 "  • 输出图与 prompt 毫不相关(例如返回了风景照)\n"
                 "\n"
                 "💡 这些情况是正常的,不要扣分:\n"
                 "  • logo 被重新着色、加阴影、加反光\n"
                 "  • logo 被略微透视变形以匹配架子角度\n"
                 "  • 画面整体艺术风格化(例如水彩、3D 渲染)"
             )),
    TestCase("C2", "C", "多图融合（image[]）", "MANUAL", True,
             _req_openai_c2, judge_name="openai_basic_image",
             manual_prompt=(
                 "📋 此用例的入参:\n"
                 "  • 参考图 3 张:① 简笔 logo  ② 商品照(如瓶罐/包装)  ③ 几何图形\n"
                 "  • prompt:\"combine these elements into a poster\"\n"
                 "\n"
                 "✅ 同时满足 → 点 通过(海报里能至少认出 2 张参考图的元素):\n"
                 "  ① 能在画面里指出参考 logo 的形状/轮廓\n"
                 "  ② 能在画面里指出参考商品的轮廓或主色\n"
                 "  ③ 能在画面里指出参考几何图形(或其衍生装饰)\n"
                 "  → 三条里命中 ≥ 2 条即视为通过\n"
                 "\n"
                 "❌ 任一 → 点 不通过:\n"
                 "  • 输出图与三张参考图全无视觉关联,完全是另一个场景\n"
                 "  • 上游只输出了三张参考图的简单拼贴(没有海报构图/排版)\n"
                 "  • 输出图严重残缺(空白、只有色块、严重马赛克)\n"
                 "\n"
                 "💡 这些情况是正常的,不要扣分:\n"
                 "  • 元素被重新着色或材质化(转 3D / 水彩等)\n"
                 "  • 缺少明显的文字排版(prompt 没要求文字)\n"
                 "  • 三张参考图的相对比例与原始图不同"
             )),
    TestCase("C3", "C", "单图 + mask 局部修改", "SEMI", True,
             _req_openai_c3, judge_name="openai_c3",
             manual_prompt=(
                 "📋 此用例的入参:\n"
                 "  • 原图:左黑色矩形 + 右浅色块的双色图(1024×1024)\n"
                 "  • mask:左半 alpha=255 (要求保留)、右半 alpha=0 (允许重画)\n"
                 "  • prompt:\"replace the right half with a sunset over hills\"\n"
                 "\n"
                 "✅ 同时满足 → 点 通过:\n"
                 "  ① 输出图的左半 仍然是深色矩形(允许色调微变,不要求像素一致)\n"
                 "  ② 输出图的右半 出现了带太阳/天空/橙色调的日落场景\n"
                 "\n"
                 "❌ 任一 → 点 不通过:\n"
                 "  • 左半被改写成日落 → mask 完全没起作用\n"
                 "  • 右半仍是原来的浅色块 → 重画没发生\n"
                 "  • 输出图与原图毫不相关(例如直接返回风景照)\n"
                 "\n"
                 "💡 这些情况是正常的,不要因此扣分:\n"
                 "  • 左半的深色变深/变浅 5–10%(中转做了接缝色调匹配)\n"
                 "  • 输出尺寸变成 1254 而不是 1024(中转放大了)\n"
                 "  • 中线接缝处有几像素的渐变带\n"
                 "\n"
                 "ℹ 上面\"左半相似度 ≈ X%\"是参考数字,真上游普遍 70–90% 之间,\n"
                 "  数字本身不能直接判定 — 凭眼睛看是否符合上面 ① ②。"
             )),
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
                 manual_prompt=(
                     "📋 此用例的入参:\n"
                     "  • prompt:\"A photo-realistic visualization of today's weather in Tokyo.\"\n"
                     "  • 启用了 google_search grounding(上游会做实时搜索后再生图)\n"
                     "\n"
                     "✅ 同时满足 → 点 通过:\n"
                     "  ① 画面是写实摄影风格的城市/街道场景(不是插画、不是地图)\n"
                     "  ② 画面里有可识别的天气线索:晴/阴/雨/雪/雾 任一种\n"
                     "  ③ 场景元素与日本/东京有关联(招牌、建筑、街道氛围之一即可)\n"
                     "\n"
                     "❌ 任一 → 点 不通过:\n"
                     "  • 画面与天气无关(例如纯产品照)\n"
                     "  • 风格非写实(简笔画、卡通、抽象)\n"
                     "  • 出现明显非东京/非日本的地标(例如埃菲尔铁塔、金门大桥)\n"
                     "\n"
                     "💡 不需要核实\"今天东京真实天气是什么\":\n"
                     "  这个用例是验证 上游能不能调用 grounding 工具,\n"
                     "  不是验证天气数据准不准。画面有任意一种合理天气状态即可。"
                 )),
        TestCase("C1", "C", "单参考图重绘", "MANUAL", True,
                 _req_gemini_ref(model_id), judge_name="gemini_basic_image",
                 manual_prompt=(
                     "📋 此用例的入参:\n"
                     "  • 参考图 1 张:一个深色简笔 logo(纯图形,无文字)\n"
                     "  • prompt:\"reimagine this logo on a billboard at night\"\n"
                     "\n"
                     "✅ 同时满足 → 点 通过:\n"
                     "  ① 画面里有明显的广告牌 / 大屏 / 灯箱结构\n"
                     "  ② 广告牌内容是参考 logo 的衍生(轮廓相似即可)\n"
                     "  ③ 整体氛围是夜景(深色背景、灯光)\n"
                     "\n"
                     "❌ 任一 → 点 不通过:\n"
                     "  • 没有广告牌或类似的承载结构\n"
                     "  • 广告牌上是无关图案,与参考 logo 无关联\n"
                     "  • 画面是白天/室内,完全没有夜景痕迹\n"
                     "\n"
                     "💡 这些情况是正常的:\n"
                     "  • logo 被重新着色、加发光、加霓虹效果\n"
                     "  • logo 被透视变形以匹配广告牌角度\n"
                     "  • logo 局部细节简化或风格化"
                 )),
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
                 manual_prompt=(
                     "📋 此用例的入参:\n"
                     "  • prompt:\"A photoreal Timareta butterfly on a flower\"\n"
                     "  • 启用了 google_search 和 image_search 工具(上游会先搜图再生图)\n"
                     "\n"
                     "✅ 同时满足 → 点 通过:\n"
                     "  ① 画面里有清晰可辨的蝴蝶,翅膀有具体的花纹/色块(不是模糊轮廓)\n"
                     "  ② 蝴蝶停在或靠近花朵\n"
                     "  ③ 风格是写实摄影/微距质感(不是简笔画或卡通)\n"
                     "\n"
                     "❌ 任一 → 点 不通过:\n"
                     "  • 画面是简笔画 / 卡通 / 剪贴画风格\n"
                     "  • 蝴蝶翅膀是单色或纯渐变,无任何花纹细节\n"
                     "  • 画面里没有蝴蝶或没有花\n"
                     "\n"
                     "💡 不需要核实\"这是不是真的 Timareta 蝴蝶\":\n"
                     "  本用例验证的是 上游能不能调用 image_search 让细节具体化,\n"
                     "  不是验证物种识别准不准。翅膀有花纹 = 工具起作用了。"
                 )),
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
