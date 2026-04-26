"""Shared helpers for image-API probes against the bltcy relay.

Strict per-key success budget enforcement: a "success" is any HTTP call that
returned an actual image payload. Failures (4xx/5xx, no image bytes) are not
counted toward the budget because the relay does not bill for them.
"""

from __future__ import annotations

import base64
import io
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import requests

BASE_URL = "https://api.bltcy.ai"
ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "outputs"
IN_DIR = ROOT / "inputs"
OUT_DIR.mkdir(exist_ok=True)
IN_DIR.mkdir(exist_ok=True)


@dataclass
class Budget:
    """Cap on successful image generations per key."""

    label: str
    max_successes: int = 5
    successes: int = 0
    attempts: int = 0
    log: list[dict[str, Any]] = field(default_factory=list)

    def can_spend(self) -> bool:
        return self.successes < self.max_successes

    def record(self, entry: dict[str, Any]) -> None:
        self.attempts += 1
        if entry.get("success"):
            self.successes += 1
        self.log.append(entry)


def _slug(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in s)[:80]


def save_image_bytes(data: bytes, *, key_label: str, test_name: str, mime: str = "image/png") -> Path:
    ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(mime, "bin")
    ts = time.strftime("%H%M%S")
    name = f"{_slug(key_label)}__{_slug(test_name)}__{ts}.{ext}"
    path = OUT_DIR / name
    path.write_bytes(data)
    return path


def measure_image(data: bytes) -> dict[str, Any]:
    """Return basic measurements (size in bytes + width/height when decodable)."""
    info: dict[str, Any] = {"bytes": len(data)}
    try:
        from PIL import Image  # type: ignore

        img = Image.open(io.BytesIO(data))
        info["width"] = img.width
        info["height"] = img.height
        info["format"] = img.format
        info["mode"] = img.mode
    except Exception as exc:  # noqa: BLE001
        info["decode_error"] = repr(exc)
    return info


# ---------- OpenAI-compatible (gpt-image-*) ----------

def openai_generate(
    *,
    api_key: str,
    model: str,
    prompt: str,
    size: str | None = None,
    quality: str | None = None,
    n: int = 1,
    extra: dict[str, Any] | None = None,
    timeout: int = 180,
) -> tuple[int, dict[str, Any] | str]:
    url = f"{BASE_URL}/v1/images/generations"
    body: dict[str, Any] = {"model": model, "prompt": prompt, "n": n}
    if size is not None:
        body["size"] = size
    if quality is not None:
        body["quality"] = quality
    if extra:
        body.update(extra)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    r = requests.post(url, headers=headers, json=body, timeout=timeout)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, r.text


def openai_edit_multipart(
    *,
    api_key: str,
    model: str,
    prompt: str,
    image_path: Path,
    size: str | None = None,
    quality: str | None = None,
    n: int = 1,
    extra: dict[str, Any] | None = None,
    timeout: int = 240,
) -> tuple[int, dict[str, Any] | str]:
    """Standard OpenAI images/edits multipart form (single image field)."""
    url = f"{BASE_URL}/v1/images/edits"
    headers = {"Authorization": f"Bearer {api_key}"}
    data: dict[str, Any] = {"model": model, "prompt": prompt, "n": str(n)}
    if size is not None:
        data["size"] = size
    if quality is not None:
        data["quality"] = quality
    if extra:
        for k, v in extra.items():
            data[k] = v if isinstance(v, (str, int, float)) else json.dumps(v)
    with image_path.open("rb") as fh:
        files = {"image": (image_path.name, fh, "image/png")}
        r = requests.post(url, headers=headers, data=data, files=files, timeout=timeout)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, r.text


def extract_openai_image(payload: Any) -> tuple[bytes | None, dict[str, Any]]:
    """Return (image_bytes, meta) from OpenAI-style image response."""
    meta: dict[str, Any] = {}
    if not isinstance(payload, dict):
        return None, meta
    data = payload.get("data") or []
    if not data:
        return None, meta
    first = data[0]
    meta["echoed_size"] = payload.get("size")
    meta["echoed_quality"] = payload.get("quality")
    meta["echoed_format"] = payload.get("output_format")
    meta["usage"] = payload.get("usage")
    if isinstance(first, dict):
        b64 = first.get("b64_json")
        url = first.get("url")
        if b64:
            try:
                return base64.b64decode(b64), meta
            except Exception as exc:  # noqa: BLE001
                meta["b64_decode_error"] = repr(exc)
        if url:
            try:
                rr = requests.get(url, timeout=120)
                if rr.ok:
                    return rr.content, {**meta, "fetched_url": url}
                meta["url_status"] = rr.status_code
            except Exception as exc:  # noqa: BLE001
                meta["url_error"] = repr(exc)
    return None, meta


# ---------- Gemini-native (nano-banana) ----------

def gemini_generate_content(
    *,
    api_key: str,
    model: str,
    prompt: str,
    aspect_ratio: str | None = None,
    image_size: str | None = None,
    input_images: list[tuple[str, bytes]] | None = None,  # list of (mime, bytes)
    auth_style: str = "bearer",  # "bearer" | "x-goog" | "query"
    response_modalities: tuple[str, ...] = ("IMAGE",),
    timeout: int = 180,
) -> tuple[int, dict[str, Any] | str]:
    """Call Google's native generateContent endpoint via the relay."""
    url = f"{BASE_URL}/v1beta/models/{model}:generateContent"
    parts: list[dict[str, Any]] = [{"text": prompt}]
    for mime, raw in input_images or []:
        parts.append({
            "inline_data": {
                "mime_type": mime,
                "data": base64.b64encode(raw).decode("ascii"),
            }
        })
    body: dict[str, Any] = {
        "contents": [{"parts": parts}],
        "generationConfig": {"responseModalities": list(response_modalities)},
    }
    image_config: dict[str, Any] = {}
    if aspect_ratio is not None:
        image_config["aspectRatio"] = aspect_ratio
    if image_size is not None:
        image_config["imageSize"] = image_size
    if image_config:
        body["generationConfig"]["imageConfig"] = image_config

    headers = {"Content-Type": "application/json"}
    params = {}
    if auth_style == "bearer":
        headers["Authorization"] = f"Bearer {api_key}"
    elif auth_style == "x-goog":
        headers["x-goog-api-key"] = api_key
    elif auth_style == "query":
        params["key"] = api_key
    r = requests.post(url, headers=headers, params=params, json=body, timeout=timeout)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, r.text


def extract_gemini_image(payload: Any) -> tuple[bytes | None, dict[str, Any]]:
    meta: dict[str, Any] = {}
    if not isinstance(payload, dict):
        return None, meta
    candidates = payload.get("candidates") or []
    if not candidates:
        meta["promptFeedback"] = payload.get("promptFeedback")
        meta["error"] = payload.get("error")
        return None, meta
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text_chunks = []
    for part in parts:
        inline = part.get("inline_data") or part.get("inlineData")
        if inline and inline.get("data"):
            mime = inline.get("mime_type") or inline.get("mimeType") or "image/png"
            try:
                data = base64.b64decode(inline["data"])
                meta["mime"] = mime
                meta["text"] = "\n".join(text_chunks) if text_chunks else None
                meta["finishReason"] = candidates[0].get("finishReason")
                meta["usageMetadata"] = payload.get("usageMetadata")
                return data, meta
            except Exception as exc:  # noqa: BLE001
                meta["b64_decode_error"] = repr(exc)
        if "text" in part:
            text_chunks.append(part["text"])
    meta["text"] = "\n".join(text_chunks) if text_chunks else None
    meta["finishReason"] = candidates[0].get("finishReason")
    return None, meta


# ---------- Probe runner ----------

def run_probe(
    *,
    budget: Budget,
    test_name: str,
    request_summary: dict[str, Any],
    call: Callable[[], tuple[int, Any]],
    extract: Callable[[Any], tuple[bytes | None, dict[str, Any]]],
    expect: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute one probe call. Refuses to run if budget is exhausted."""
    if not budget.can_spend():
        entry = {
            "test": test_name,
            "skipped": "budget exhausted",
            "request": request_summary,
        }
        budget.log.append(entry)
        print(f"[{budget.label}] SKIP {test_name} (budget {budget.successes}/{budget.max_successes})")
        return entry

    print(f"[{budget.label}] RUN  {test_name}  (success {budget.successes}/{budget.max_successes})")
    t0 = time.time()
    try:
        status, payload = call()
    except Exception as exc:  # noqa: BLE001
        entry = {
            "test": test_name,
            "request": request_summary,
            "transport_error": repr(exc),
            "elapsed_s": round(time.time() - t0, 2),
            "success": False,
        }
        budget.record(entry)
        print(f"  -> transport error: {exc}")
        return entry
    elapsed = round(time.time() - t0, 2)

    img_bytes, meta = extract(payload)
    success = img_bytes is not None
    entry: dict[str, Any] = {
        "test": test_name,
        "request": request_summary,
        "http_status": status,
        "elapsed_s": elapsed,
        "response_meta": meta,
        "success": success,
    }

    if not success:
        # Trim huge payloads
        snip = payload
        if isinstance(payload, dict):
            try:
                snip = json.loads(json.dumps(payload, default=str)[:4000])
            except Exception:  # noqa: BLE001
                snip = str(payload)[:4000]
        elif isinstance(payload, str):
            snip = payload[:4000]
        entry["error_payload"] = snip
        budget.record(entry)
        print(f"  -> FAIL status={status} elapsed={elapsed}s meta_keys={list(meta)[:6]}")
        return entry

    measured = measure_image(img_bytes)
    saved = save_image_bytes(img_bytes, key_label=budget.label, test_name=test_name,
                             mime=meta.get("mime", "image/png"))
    entry["image"] = {"path": str(saved.relative_to(ROOT)), **measured}
    if expect is not None:
        entry["expect"] = expect
        entry["match"] = _check_match(measured, expect)

    budget.record(entry)
    desc = f"{measured.get('width')}x{measured.get('height')} {measured.get('format')}" \
        if "width" in measured else f"{measured.get('bytes')} bytes"
    print(f"  -> OK  {desc}  saved={saved.name}")
    if entry.get("match") and not entry["match"]["ok"]:
        print(f"     ! mismatch: {entry['match']}")
    return entry


def _check_match(measured: dict[str, Any], expect: dict[str, Any]) -> dict[str, Any]:
    """Compare actual image dims to user expectation (tolerant of off-by-a-few)."""
    out: dict[str, Any] = {"ok": True, "notes": []}
    w, h = measured.get("width"), measured.get("height")
    ew, eh = expect.get("width"), expect.get("height")
    if ew is not None and eh is not None and (w, h) != (None, None):
        # Accept exact match, or aspect-ratio match within 1%
        if (w, h) == (ew, eh):
            out["notes"].append(f"exact {w}x{h}")
        else:
            out["ok"] = False
            out["notes"].append(f"got {w}x{h}, expected {ew}x{eh}")
    if "min_long_edge" in expect and (w and h):
        if max(w, h) < expect["min_long_edge"]:
            out["ok"] = False
            out["notes"].append(f"long edge {max(w, h)} < min {expect['min_long_edge']}")
    return out


def write_report(name: str, budgets: list[Budget]) -> Path:
    report = {
        "base_url": BASE_URL,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "budgets": [
            {
                "label": b.label,
                "max_successes": b.max_successes,
                "successes": b.successes,
                "attempts": b.attempts,
                "log": b.log,
            }
            for b in budgets
        ],
    }
    path = OUT_DIR / name
    path.write_text(json.dumps(report, indent=2, default=str))
    return path
