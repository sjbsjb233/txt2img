# bltcy.ai · `gpt-image-2`

bltcy 把 `gpt-image-2` 暴露成 **OpenAI 原生 Images API**，所以 JSON / multipart
结构与 [OpenAI 官方文档](https://developers.openai.com/api/reference/resources/images/methods/generate)
保持一致。

## 端点

```
POST https://api.bltcy.ai/v1/images/generations    (Content-Type: application/json)
POST https://api.bltcy.ai/v1/images/edits          (Content-Type: multipart/form-data)
Authorization: Bearer sk-...
```

## 请求参数（generations）

| 参数 | 类型 | 备注 |
| --- | --- | --- |
| `model` | string | `gpt-image-2`（中转站对此模型名兼容；上游可能是 `gpt-image-1.5` / `gpt-image-1`） |
| `prompt` | string | 必填 |
| `size` | string | `1024x1024`、`1024x1536`、`1536x1024`，高分辨率档可达长边 ≤ 3840 |
| `quality` | string | `auto` / `low` / `medium` / `high` |
| `n` | int | 1–10 |
| `background` | string | `auto` / `transparent` / `opaque`（仅 png/webp） |
| `output_format` | string | `png` / `jpeg` / `webp` |
| `output_compression` | int | 0–100，仅 jpeg/webp |
| `moderation` | string | `auto` / `low` |
| `user` | string | 终端用户标识，用于滥用审查 |

## 请求参数（edits）

`multipart/form-data`：

- `model`（form field）
- `prompt`（form field）
- `image`（file，必填，单张；当前未验证 `images[]` 多图形式在 bltcy 是否可用）
- `mask`（file，可选；尺寸需与 `image` 一致，透明区域将被重绘）
- 其余参数与 generations 相同：`size`、`quality`、`background`、`output_format`、`output_compression`、`input_fidelity`（`high`/`low`）、`n`、`user`

## 响应体

```jsonc
{
  "created": 1713833628,
  "data": [
    {
      "b64_json": "<BASE64>",   // 中转站默认走 b64
      "revised_prompt": null
    }
  ],
  "usage": {
    "input_tokens": 230,
    "output_tokens": 560,
    "total_tokens": 790,
    "input_tokens_details": { "text_tokens": 230, "image_tokens": 0 },
    "output_tokens_details": { "image_tokens": 560 }
  }
  // 注意：bltcy 的成功响应通常 不 回显 size / quality / output_format
}
```

## 双档密钥

bltcy 把"低分辨率档"和"高分辨率档" `gpt-image-2` 路由到不同上游，**同样的请求行为完全不同**：

### 低分辨率档（key1）

| 请求 | 实际返回 | 说明 |
| --- | --- | --- |
| `1024x1024`、`quality=low` | **1254×1254 PNG** | size 被忽略 |
| `1024x1024`、`quality=high` | **1254×1254 PNG** | size 被忽略 |
| `2048x2048`、`quality=high` | **1254×1254 PNG** | size 被忽略 |
| `4096x4096`、`quality=high` | **1024×1024 PNG**（或 1254×1254，不确定） | size 被忽略，且分辨率不稳定 |
| `1024x1536`、`quality=low` | **1024×1536 PNG** ✅ | 唯一被尊重的尺寸 |
| `images.edits` `1024x1024` | **1254×1254 PNG** | size 被忽略 |

- `output_tokens` 在所有请求里都是 560，**`quality` 没有任何观测得到的效果**。
- 适合做"快速预览"，但不要承诺给用户特定分辨率。

### 高分辨率档（key2）

- 上游会**严格校验 `size`**：长边 > 3840 直接 HTTP 400：
  ```
  Invalid size '4096x4096'. The longest edge must be less than or equal to 3840.
  ```
  → 这条错误消息可以反向证明上游确实是高分辨率渲染器，与低分辨率档不同。
- **当前不可用**：所有请求在 ~125 秒被 Cloudflare 524 切断；`/v1/images/edits`
  在 ~120 秒返回 408 timeout。集成前**先联系 bltcy 客服确认上游是否恢复**。
- 集成时尺寸建议：
  - 1K 方图：`1024x1024`
  - 2K 方图：`2048x2048`
  - 4K 横屏：`3840x2160`（**不要写 4096**）
  - 4K 竖屏：`2160x3840`

## 客户端使用

后端封装见 [`backend/app/image_clients/bltcy_gpt_image_2.py`](../app/image_clients/bltcy_gpt_image_2.py)。

### 文本生图

```python
from app.image_clients import BltcyGptImage2Client

async with BltcyGptImage2Client(api_key=key) as gpt:
    res = await gpt.generate(
        prompt="A photorealistic still life: orange juice and kiwi on marble.",
        size="1024x1024",
        quality="high",
        output_format="png",
    )

# res.image_bytes      -> bytes
# res.mime_type        -> "image/png" / "image/jpeg" / "image/webp"
# res.usage            -> dict（input_tokens / output_tokens / image_tokens）
# res.echoed_size      -> 中转站常返回 None；不要据此判断真实尺寸
# res.revised_prompt   -> 仅 DALL-E 3 类模型会返回
```

### 图编辑

```python
from pathlib import Path

async with BltcyGptImage2Client(api_key=key) as gpt:
    res = await gpt.edit(
        prompt="Replace the yellow sun with a full moon at night.",
        image=Path("input.png"),
        size="1024x1024",
        quality="high",
        background="opaque",
    )
```

也可以直接传 bytes：

```python
res = await gpt.edit(
    prompt="...",
    image=raw_bytes,
    image_filename="user_upload.png",
    image_mime="image/png",
    mask=mask_bytes,           # 可选：透明区域将被重绘
)
```

### 共享 httpx 客户端

```python
shared = httpx.AsyncClient(timeout=600.0)
gpt = BltcyGptImage2Client(api_key=key, client=shared)
# ...
await shared.aclose()
```

### 错误处理

```python
from app.image_clients.bltcy_gpt_image_2 import BltcyGptImage2Error

try:
    res = await gpt.generate(...)
except BltcyGptImage2Error as e:
    if e.status == 400 and "longest edge" in (e.message or ""):
        # 高分辨率档拒绝了 size，回退到 3840
        ...
    elif e.status in (408, 524):
        # 上游超时，过几分钟再重试或排队处理
        ...
    else:
        raise
```

## 与官方 OpenAI 行为的差异

| 行为 | OpenAI 官方 | bltcy 低分辨率档 | bltcy 高分辨率档 |
| --- | --- | --- | --- |
| 拒绝非法 `size` | 400 + 明确错误 | ❌ 静默忽略 | ✅ 400 长边错误 |
| `quality=high` 涨价/涨 token | ✅ | ❌（恒 560） | （未观测到成功响应） |
| 响应回显 `size` / `quality` | ✅ | ❌（常 `None`） | （未观测） |
| `revised_prompt` 字段 | DALL-E 3 才有 | 始终 `null` | （未观测） |
| `images.edits` 多图 (`images[]`) | ✅ 最多 16 张 | 未验证 | 未验证 |

**实践建议**：写后端时不要假设 OpenAI 标准行为，统一用解码出的真实图像作为
`width` / `height` 的事实来源；用客户端代码里的 `GptImageResult.raw` 字段记录
完整原始响应方便事后排查。

## 计费提示

- 实测低分辨率档每次成功调用 `output_tokens = 560`，与 `size` / `quality` 无关。
- `images.edits` 会额外计 `image_tokens`（输入图占 ~560 input_tokens · image）。
- 高分辨率档计费未观测到，集成前应通过小批量测试估算。
