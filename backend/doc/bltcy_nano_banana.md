# bltcy.ai · Nano Banana / Gemini 图像模型

bltcy 转发的是 **Google Gemini 原生 REST 接口**，而不是 OpenAI 兼容形态，
所以 JSON 结构与 [Google AI for Developers 官方文档](https://ai.google.dev/gemini-api/docs/image-generation) 保持一致。

## 端点

```
POST https://api.bltcy.ai/v1beta/models/{model}:generateContent
Authorization: Bearer sk-...
Content-Type: application/json
```

## 可用模型

bltcy 上验证可访问的三种 Nano Banana 模型：

| `model` | 别名 | 输出格式 | 主要用途 |
| --- | --- | --- | --- |
| `gemini-2.5-flash-image` | Nano Banana | PNG | 最便宜，但分辨率受限 |
| `gemini-3-pro-image-preview` | Nano Banana Pro | JPEG | 质量最高，可控分辨率 |
| `gemini-3.1-flash-image-preview` | Nano Banana 2 | JPEG | 速度与质量平衡 |

## 请求体

```jsonc
{
  "contents": [
    {
      "parts": [
        { "text": "your prompt here" },
        // 编辑场景：附加输入图（base64）
        {
          "inline_data": {
            "mime_type": "image/png",
            "data": "<BASE64_BYTES>"
          }
        }
      ]
    }
  ],
  "generationConfig": {
    "responseModalities": ["IMAGE"],
    "imageConfig": {
      "aspectRatio": "16:9",   // 可选
      "imageSize": "2K"         // 可选，仅 Pro / 3.1-flash 真正生效
    }
  }
}
```

`aspectRatio` 支持值（实测可用）：
`1:1`、`16:9`、`9:16`、`4:3`、`3:4`、`3:2`、`2:3`、`21:9`、`4:5`、`5:4` 等。

`imageSize` 支持值：`512`、`1K`、`2K`、`4K`。

## 响应体

```jsonc
{
  "candidates": [
    {
      "content": {
        "parts": [
          {
            "inline_data": {
              "mime_type": "image/png",   // 或 image/jpeg
              "data": "<BASE64_BYTES>"
            }
          }
        ]
      },
      "finishReason": "STOP"
    }
  ],
  "usageMetadata": {
    "promptTokenCount": 13,
    "candidatesTokenCount": 1290,
    "totalTokenCount": 1303,
    "candidatesTokensDetails": [
      { "modality": "IMAGE", "tokenCount": 1290 }
    ]
  }
}
```

图片字节藏在 `candidates[0].content.parts[*].inline_data.data`（base64）。
注意客户端代码要兼容两种字段名 (`inline_data` / `inlineData`，`mime_type` / `mimeType`)。

## 实测行为差异（关键！）

| 模型 | `aspectRatio` 是否生效 | `imageSize` 是否生效 | 备注 |
| --- | --- | --- | --- |
| `gemini-2.5-flash-image` | ✅ | ❌（始终 ~1MP） | 16:9 / 1K 请求实际给出 **1344×768**；任何 `imageSize` 都被无视 |
| `gemini-3-pro-image-preview` | ✅ | ✅ | 1:1 / 4K → **4096×4096**；16:9 / 4K → **5504×3072**；21:9 / 2K → **3168×1344**；实际像素经常**比标称更大** |
| `gemini-3.1-flash-image-preview` | ✅ | ✅ | 16:9 / 2K → **2752×1536**；9:16 / 4K → **3072×5504** |

**结论**：
- 需要可控分辨率 → 用 Pro 或 3.1-flash。
- 需要省钱、对分辨率没要求 → 用 2.5-flash。
- 三种模型的 `edit()`（带输入图）都能工作；`1:1 / 1K` 输入返回 `1024×1024` 输出。
- 不要根据请求的 `imageSize` 在前端预设画布尺寸；以解码出的真实图像为准。

## 客户端使用

后端封装见 [`backend/app/image_clients/bltcy_nano_banana.py`](../app/image_clients/bltcy_nano_banana.py)。

### 文本生图

```python
from app.image_clients import BltcyNanoBananaClient

async with BltcyNanoBananaClient(api_key=settings.BLTCY_NANOBANANA_KEY) as nano:
    res = await nano.generate(
        prompt="A cinematic shot of an astronaut in a flower field under twin moons.",
        model="gemini-3-pro-image-preview",
        aspect_ratio="16:9",
        image_size="4K",
    )

# res.image_bytes  -> bytes (JPEG 或 PNG)
# res.mime_type    -> "image/jpeg" / "image/png"
# res.usage        -> dict（含 token 用量）
```

### 图生图 / 编辑

```python
input_bytes = Path("source.png").read_bytes()

async with BltcyNanoBananaClient(api_key=key) as nano:
    res = await nano.edit(
        prompt="Replace the sun with a glowing full moon at night.",
        input_images=[("image/png", input_bytes)],
        model="gemini-3-pro-image-preview",
        aspect_ratio="1:1",
        image_size="1K",
    )
```

支持多张输入图：`input_images` 接受 `(mime, bytes)` 列表。

### 注入共享 httpx 客户端

适合服务长生命周期的场景（例如 FastAPI 启动时建一个，多个 endpoint 复用）：

```python
shared = httpx.AsyncClient(timeout=600.0)
nano = BltcyNanoBananaClient(api_key=key, client=shared)
# ...
await shared.aclose()
```

这种用法下 `aclose()` 不会关闭注入的 client，调用方需要自己关闭。

### 错误处理

```python
from app.image_clients.bltcy_nano_banana import BltcyNanoBananaError

try:
    res = await nano.generate(...)
except BltcyNanoBananaError as e:
    log.warning("nano-banana failed: status=%s msg=%s", e.status, e.message)
    # e.payload 是上游原始 JSON，可以读 e.payload.get("error", {}).get("code")
```

## 计费提示

- `usageMetadata.candidatesTokensDetails[].tokenCount` 是图像 token，本次实测：
  - `gemini-2.5-flash-image`: ~1290
  - `gemini-3-pro-image-preview`: ~1120（含 thoughtsTokenCount ~150）
  - `gemini-3.1-flash-image-preview`: ~1120
- Pro 和 3.1-flash 的图像 token 数与请求的 `imageSize` 无关，**调到 4K 也不额外
  收钱**。这是 bltcy 上的实测，与 Google 官方按分辨率分级计费的描述不一致——
  推测是 bltcy 对上游做了打包定价。
