"""Mock upstream that captures every request the txt2img backend sends.

Run with::

    uvicorn scripts.playwright_test.capture_server:app --port 18890

Exposes the wire formats both real upstreams use so the txt2img backend
can be pointed at it with no other changes:

- POST /v1/images/generations / edits  (OpenAI gpt-image-2)
- POST /v1beta/models/{model}:generateContent (Gemini)

After the backend has sent a request, the test code reads it back via::

    GET    /__captures      -> list of {kind, path, body, multipart, headers, model}
    DELETE /__captures      -> wipe the buffer
"""

from __future__ import annotations

import base64
import io
import json
import time
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from PIL import Image

app = FastAPI(title="txt2img upstream capture")

_CAPTURES: list[dict[str, Any]] = []


def _png_bytes() -> bytes:
    """Tiny PNG so adapters can decode a non-empty image."""
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color=(180, 200, 220)).save(buf, format="PNG")
    return buf.getvalue()


def _png_b64() -> str:
    return base64.b64encode(_png_bytes()).decode("ascii")


@app.get("/__captures")
async def list_captures() -> dict[str, Any]:
    return {"items": list(_CAPTURES)}


@app.delete("/__captures")
async def clear_captures() -> dict[str, Any]:
    _CAPTURES.clear()
    return {"ok": True}


@app.get("/__healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# OpenAI ``/v1/images/generations``
# ---------------------------------------------------------------------------


@app.post("/v1/images/generations")
async def openai_generations(request: Request) -> JSONResponse:
    body = await request.body()
    try:
        parsed = json.loads(body.decode("utf-8"))
    except Exception:
        parsed = {"_raw": body.decode("utf-8", errors="replace")}
    _CAPTURES.append(
        {
            "id": uuid.uuid4().hex,
            "kind": "openai_generations",
            "path": "/v1/images/generations",
            "headers": dict(request.headers),
            "json": parsed,
            "model": parsed.get("model"),
            "ts": time.time(),
        }
    )
    return JSONResponse(
        {
            "created": int(time.time()),
            "data": [
                {"b64_json": _png_b64()}
                for _ in range(int(parsed.get("n") or 1))
            ],
        }
    )


@app.post("/v1/images/edits")
async def openai_edits(request: Request) -> JSONResponse:
    form = await request.form()
    fields: dict[str, Any] = {}
    for key, value in form.multi_items():
        if hasattr(value, "filename"):
            fields.setdefault(key, []).append(
                {
                    "filename": value.filename,
                    "content_type": value.content_type,
                    "size": len(await value.read()),
                }
            )
        else:
            fields[key] = value
    _CAPTURES.append(
        {
            "id": uuid.uuid4().hex,
            "kind": "openai_edits",
            "path": "/v1/images/edits",
            "headers": dict(request.headers),
            "form": fields,
            "model": fields.get("model"),
            "ts": time.time(),
        }
    )
    return JSONResponse(
        {
            "created": int(time.time()),
            "data": [
                {"b64_json": _png_b64()}
                for _ in range(int(fields.get("n") or 1))
            ],
        }
    )


# ---------------------------------------------------------------------------
# Gemini ``/v1beta/models/<model>:generateContent``
# ---------------------------------------------------------------------------


@app.post("/v1beta/models/{model_path:path}")
async def gemini_generate(model_path: str, request: Request) -> JSONResponse:
    body = await request.body()
    try:
        parsed = json.loads(body.decode("utf-8"))
    except Exception:
        parsed = {"_raw": body.decode("utf-8", errors="replace")}
    model_id = model_path.split(":")[0] if ":" in model_path else model_path
    _CAPTURES.append(
        {
            "id": uuid.uuid4().hex,
            "kind": "gemini_generate",
            "path": f"/v1beta/models/{model_path}",
            "headers": dict(request.headers),
            "json": parsed,
            "model": model_id,
            "ts": time.time(),
        }
    )
    return JSONResponse(
        {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "ok"},
                            {
                                "inline_data": {
                                    "mime_type": "image/png",
                                    "data": _png_b64(),
                                }
                            },
                        ]
                    },
                    "finishReason": "STOP",
                }
            ]
        }
    )
