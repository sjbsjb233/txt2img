# bltcy.ai 中转站接入文档

本目录记录后端如何对接 **bltcy.ai** 中转站，以及实际探针测试得出的**真实行为**（与官方文档存在出入的部分）。

## 文件索引

- [`README.md`](./README.md) — 本文：总览、密钥管理、通用约定。
- [`bltcy_nano_banana.md`](./bltcy_nano_banana.md) — Google Nano Banana / Gemini 系列图像模型对接细节。
- [`bltcy_gpt_image_2.md`](./bltcy_gpt_image_2.md) — OpenAI `gpt-image-2` 对接细节（含双档密钥差异）。

## 中转站基本信息

| 项 | 值 |
| --- | --- |
| Base URL | `https://api.bltcy.ai` |
| 鉴权方式 | `Authorization: Bearer sk-...`（OpenAI 风格，全平台统一） |
| 兼容协议 | OpenAI 原生 (`/v1/images/...`)、Google Gemini 原生 (`/v1beta/models/{model}:generateContent`) |
| 计费模型 | 按上游模型 token 计；**只对成功返回的请求计费**（4xx/5xx、网关 524 不计费） |

> 即便上游是 Google，bltcy 也接受 `Authorization: Bearer` 风格。原生的
> `x-goog-api-key` 头和 `?key=` query 参数也支持，但客户端默认用 Bearer，
> 这样所有 key 形式统一。

## 密钥分档

bltcy 不同密钥被路由到不同的上游档位，**同一个模型名在不同密钥下的行为可能完全不同**：

| 密钥用途 | 可访问模型 | 备注 |
| --- | --- | --- |
| Nano Banana 综合密钥 | `gemini-2.5-flash-image`、`gemini-3-pro-image-preview`、`gemini-3.1-flash-image-preview` 全部三个 | 详见 nano-banana 文档 |
| `gpt-image-2` 低分辨率档 | `gpt-image-2` | `size`/`quality` 参数大多被忽略，实际固定输出 ~1254×1254 |
| `gpt-image-2` 高分辨率档 | `gpt-image-2` | 真实高分辨率上游，严格校验 size（长边 ≤ 3840），但目前网关延迟严重 |

详细行为差异见各模型的文档。

## 后端代码入口

所有客户端位于 [`backend/app/image_clients/`](../app/image_clients/)，是基于 `httpx.AsyncClient` 的轻量异步封装：

```python
from app.image_clients import BltcyNanoBananaClient, BltcyGptImage2Client

async with BltcyNanoBananaClient(api_key=key) as nano:
    res = await nano.generate("...", model="gemini-3-pro-image-preview",
                              aspect_ratio="16:9", image_size="2K")

async with BltcyGptImage2Client(api_key=key) as gpt:
    res = await gpt.edit("...", image=Path("input.png"),
                         size="1024x1024", quality="high")
```

返回值是 dataclass，包含 `image_bytes`、`mime_type`、`usage`、`raw`（原始响应）等字段。
HTTP ≥ 400 会抛出 `BltcyNanoBananaError` / `BltcyGptImage2Error`，异常对象暴露 `status`、`message`、`payload`。

## 通用工程注意事项

1. **客户端超时务必拉长。** 高分辨率档（尤其 2K/4K）单次出图常超过 60 秒；
   bltcy 的 Cloudflare 网关在 ~125 秒会回 524。后端默认 `timeout=600.0`，
   实际生产环境建议把任务跑在异步队列里（例如 Celery、ARQ），不要让 HTTP
   请求线程被卡住。
2. **失败重试要谨慎。** 由于失败不计费，重试是安全的，但 524 通常代表上游
   过载，立刻重试会持续触发；建议使用指数退避（30s → 60s → 120s）。
3. **不要把 key 放进代码或日志。** 生产环境读 `.env`；探针脚本读环境变量
   （`BLTCY_NANOBANANA_KEY`、`BLTCY_GPT_IMAGE_KEY1`、`BLTCY_GPT_IMAGE_KEY2`）。
4. **响应里的 `size` / `quality` 字段可能为 `None`。** 实测 bltcy 的成功响应
   常不回显这些字段，所以**不要用 `echoed_size` 做断言**，应该解码图片字节
   去拿真实分辨率。
5. **`gpt-image-2` 在中转站接受多种"模型别名"。** 上游对应的可能是 `gpt-image-1`、
   `gpt-image-1.5` 等之一；模型名只是路由 key，不要据此推断真实上游。

## 探针 / 回归测试

`probes/` 目录下有完整的探针脚本，记录了每个密钥实际的输出尺寸、token 消耗和
异常信息。如果未来怀疑 bltcy 行为变更，重新跑 `probes/probe_round2.py` 即可
对比；脚本已经做了 5 次成功额度的硬限制。

## 已知遗留问题

- **`gpt-image-2` 高分辨率档当前不可用**：所有请求在 ~126 秒被 Cloudflare 524
  切断（详见探针报告 `probes/outputs/report_round2_key2_retry.json`）。
  建议联系 bltcy 客服确认上游状态后再尝试集成。
