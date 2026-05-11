"""Tiny SPA-fallback static server for the built frontend.

Usage: ``python static_serve.py <port> <dist_dir>``

Falls back to ``index.html`` for any non-file path so React Router routes
like ``/create`` resolve when the user navigates directly.
"""

from __future__ import annotations

import os
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5174
    dist_arg = sys.argv[2] if len(sys.argv) > 2 else "frontend/dist"
    dist = Path(dist_arg)
    dist = dist if dist.is_absolute() else (Path.cwd() / dist)
    dist = dist.resolve()
    if not dist.exists():
        print(f"static_serve: dist dir not found: {dist}", file=sys.stderr)
        return 1
    os.chdir(dist)

    class Handler(SimpleHTTPRequestHandler):
        def translate_path(self, path: str) -> str:  # type: ignore[override]
            resolved = Path(super().translate_path(path))
            if resolved.is_file():
                return str(resolved)
            return str(dist / "index.html")

        def log_message(self, *_a, **_k) -> None:  # type: ignore[override]
            pass

    server = HTTPServer(("127.0.0.1", port), Handler)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
