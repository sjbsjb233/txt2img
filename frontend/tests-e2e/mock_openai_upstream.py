"""Stand-in for ``api.openai.com/v1/images/generations`` for Playwright e2e.

Listens on ``127.0.0.1:18902`` (override with ``MOCK_PORT``). Each
incoming POST is appended to ``$MOCK_LOG`` (default
``/tmp/mock_openai_upstream.jsonl``) so the test runner can read the
exact body our backend forwarded — including ``thinking``, ``size``,
and authorization headers — and assert against it.

Always returns a tiny solid-yellow PNG via the canonical OpenAI
response shape ``{"data": [{"b64_json": "..."}]}`` so the adapter's
parser is happy.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import sys
import time
from pathlib import Path

from PIL import Image
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
import uvicorn


PORT = int(os.environ.get("MOCK_PORT", "18902"))
LOG_PATH = Path(os.environ.get("MOCK_LOG", "/tmp/mock_openai_upstream.jsonl"))


def _png_b64(color: str = "#FFD400", size: tuple[int, int] = (32, 32)) -> str:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


_PNG = _png_b64()


async def _record_and_respond(request: Request) -> JSONResponse:
    raw = await request.body()
    try:
        body = json.loads(raw.decode("utf-8"))
    except Exception:
        body = {"_raw": raw.decode("utf-8", errors="replace")[:500]}
    headers = {
        k: v
        for k, v in request.headers.items()
        # Strip transient hop-by-hop noise; keep auth/api-key for verification.
        if k.lower() not in ("host", "connection", "content-length")
    }
    record = {
        "ts": time.time(),
        "method": request.method,
        "path": request.url.path,
        "body": body,
        "headers": headers,
    }
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return JSONResponse(
        {
            "created": int(time.time()),
            "data": [{"b64_json": _PNG}],
            "usage": {"input_tokens": 21, "output_tokens": 196, "total_tokens": 217},
        }
    )


app = Starlette(
    routes=[
        Route("/v1/images/generations", _record_and_respond, methods=["POST"]),
        # Some adapters probe /v1/models or similar — we accept any path
        # and return the same OK response so a misconfigured probe
        # doesn't crash the test.
        Route("/{rest:path}", _record_and_respond, methods=["POST", "GET"]),
    ]
)


def main() -> None:
    # Truncate any stale log so a single run is self-contained.
    if LOG_PATH.exists():
        LOG_PATH.unlink()
    print(f"mock upstream up on http://127.0.0.1:{PORT} log={LOG_PATH}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
