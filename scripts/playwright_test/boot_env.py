"""Boot a self-contained test environment for the create-page Playwright test.

Steps:

1. Start the capture server (uvicorn) on $CAPTURE_PORT (default 18890).
2. Start the txt2img backend (uvicorn) on $BACKEND_PORT (default 18900)
   pointing at a temp DB / DATA_ROOT.
3. Wait for both to be up.
4. Log in as admin and seed:
   - one ``openai_v1`` provider pointing at the capture server
   - one ``gemini_v1beta`` provider pointing at the capture server
   - a test user ``e2e_tester`` with huge override quotas
5. Print a one-line JSON summary on stdout (so the caller can pick up
   ports / credentials) and then idle until killed.

Run with::

    .venv/bin/python -m scripts.playwright_test.boot_env

Override defaults via env vars: BACKEND_PORT, CAPTURE_PORT, TMPDIR.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
BACKEND_PORT = int(os.environ.get("BACKEND_PORT", "18900"))
CAPTURE_PORT = int(os.environ.get("CAPTURE_PORT", "18890"))


def _http_json(url: str, *, method: str = "GET", data: dict | None = None,
               headers: dict | None = None, timeout: float = 10.0) -> dict:
    body = None
    h = {"Accept": "application/json"}
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        h["Content-Type"] = "application/json"
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def _wait_for(url: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2.0).read()
            return
        except Exception as exc:
            last_err = exc
            time.sleep(0.4)
    raise RuntimeError(f"timed out waiting for {url}: {last_err}")


def _gpt_provider() -> dict:
    return {
        "provider_id": "oai_capture",
        "label": "OpenAI (capture)",
        "adapter_type": "openai_v1",
        "base_url": f"http://127.0.0.1:{CAPTURE_PORT}/v1",
        "api_key": "sk-fake-CAPTURE1234",
        "cost_per_image_cny": 0.10,
        "initial_balance_cny": 999999.0,
        "supported_models": [
            {
                "model_id": "gpt-image-2",
                "capabilities": {
                    "n_max": 10,
                    "size": ["1024x1024", "1536x1024", "1024x1536", "auto"],
                    "quality": ["low", "medium", "high", "auto"],
                    "output_format": ["png", "jpeg", "webp"],
                    "background": ["auto", "opaque"],
                    "moderation": ["auto", "low"],
                    "thinking": ["off", "low", "medium", "high"],
                    "max_reference_images": 16,
                    "max_prompt_chars": 32000,
                    "supports_mask": True,
                    "stream": False,
                    "partial_images_max": 0,
                    "size_allow_custom": True,
                },
            }
        ],
        "tier_access": ["vip", "premium", "standard", "free"],
        "max_concurrency": 4,
        "rpm_limit": 1000,
    }


def _gemini_provider() -> dict:
    return {
        "provider_id": "gem_capture",
        "label": "Gemini (capture)",
        "adapter_type": "gemini_v1beta",
        "base_url": f"http://127.0.0.1:{CAPTURE_PORT}/v1beta",
        "api_key": "fake-CAPTURE-gemini-key",
        "cost_per_image_cny": 0.05,
        "initial_balance_cny": 999999.0,
        "supported_models": [
            {
                "model_id": "gemini-3-pro-image-preview",
                "capabilities": {
                    "aspect_ratio": [
                        "1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4",
                        "9:16", "16:9", "21:9",
                    ],
                    "image_size": ["1K", "2K", "4K"],
                    "max_reference_images": 14,
                    "max_prompt_chars": 32000,
                    "google_search": True,
                },
            },
            {
                "model_id": "gemini-3.1-flash-image-preview",
                "capabilities": {
                    "aspect_ratio": [
                        "1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4",
                        "9:16", "16:9", "21:9",
                        "1:4", "4:1", "1:8", "8:1",
                    ],
                    "image_size": ["512", "1K", "2K", "4K"],
                    "thinking_level": ["minimal", "high"],
                    "max_reference_images": 14,
                    "max_prompt_chars": 32000,
                    "include_thoughts": True,
                    "google_search": True,
                    "image_search": True,
                },
            },
        ],
        "tier_access": ["vip", "premium", "standard", "free"],
        "max_concurrency": 4,
        "rpm_limit": 1000,
    }


def main() -> int:
    tmpdir = Path(tempfile.mkdtemp(prefix="txt2img_e2e_"))
    data_root = tmpdir / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    db_path = tmpdir / "txt2img.db"

    env = os.environ.copy()
    env.setdefault("JWT_SECRET", "test_secret_at_least_16_chars_long_value")
    env.setdefault("ADMIN_USERNAME", "admin")
    env.setdefault("ADMIN_PASSWORD", "admin-pass-1234")
    env["DB_URL"] = f"sqlite+aiosqlite:///{db_path}"
    env["DATA_ROOT"] = str(data_root)
    env["FORCE_CAPTCHA"] = "false"
    env["TURNSTILE_SITE_KEY"] = ""
    env["TURNSTILE_SECRET"] = ""
    env["PYTHONPATH"] = str(REPO / "backend") + os.pathsep + env.get("PYTHONPATH", "")

    log_dir = REPO / ".e2e-logs"
    log_dir.mkdir(exist_ok=True)
    capture_log = (log_dir / "capture.log").open("w")
    backend_log = (log_dir / "backend.log").open("w")

    cap_proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "scripts.playwright_test.capture_server:app",
            "--host", "127.0.0.1", "--port", str(CAPTURE_PORT),
            "--log-level", "warning",
        ],
        cwd=REPO, env=env, stdout=capture_log, stderr=subprocess.STDOUT,
    )

    backend_proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "app.main:create_app",
            "--factory",
            "--host", "127.0.0.1", "--port", str(BACKEND_PORT),
            "--log-level", "warning",
        ],
        cwd=REPO / "backend", env=env, stdout=backend_log, stderr=subprocess.STDOUT,
    )

    procs = [cap_proc, backend_proc]

    def _shutdown(*_a):
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass
        for p in procs:
            try:
                p.wait(timeout=5)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        capture_log.close()
        backend_log.close()
        shutil.rmtree(tmpdir, ignore_errors=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        _wait_for(f"http://127.0.0.1:{CAPTURE_PORT}/__healthz", timeout=20)
        _wait_for(f"http://127.0.0.1:{BACKEND_PORT}/api/health", timeout=40)

        # Login admin.
        admin = _http_json(
            f"http://127.0.0.1:{BACKEND_PORT}/api/auth/login",
            method="POST",
            data={"username": env["ADMIN_USERNAME"], "password": env["ADMIN_PASSWORD"]},
        )
        admin_token = admin["access_token"]
        auth = {"Authorization": f"Bearer {admin_token}"}

        # Seed providers.
        _http_json(
            f"http://127.0.0.1:{BACKEND_PORT}/api/admin/providers",
            method="POST", data=_gpt_provider(), headers=auth,
        )
        _http_json(
            f"http://127.0.0.1:{BACKEND_PORT}/api/admin/providers",
            method="POST", data=_gemini_provider(), headers=auth,
        )

        # Seed a pool of test users with massive quotas so the per-user
        # burst rule (≥5 jobs in 60s ⇒ captcha) never trips while the
        # Playwright suite drives several submissions in a row.
        password = "tester-pass-1234"
        usernames = [f"e2e_tester_{i:02d}" for i in range(20)]
        for username in usernames:
            _http_json(
                f"http://127.0.0.1:{BACKEND_PORT}/api/admin/users",
                method="POST",
                data={
                    "username": username,
                    "password": password,
                    "role": "user",
                    "tier": "vip",
                    "override_soft_quota": 100000,
                    "override_hard_quota": 100000,
                },
                headers=auth,
            )

        summary = {
            "backend": f"http://127.0.0.1:{BACKEND_PORT}",
            "capture": f"http://127.0.0.1:{CAPTURE_PORT}",
            "usernames": usernames,
            "password": password,
            "admin_username": env["ADMIN_USERNAME"],
            "admin_password": env["ADMIN_PASSWORD"],
            "tmpdir": str(tmpdir),
        }
        print("READY " + json.dumps(summary), flush=True)
    except Exception as exc:
        print(f"BOOT_ERROR {exc}", file=sys.stderr, flush=True)
        _shutdown()

    # Idle.
    while True:
        for p in procs:
            if p.poll() is not None:
                print(
                    f"BOOT_ERROR: subprocess exited with code {p.returncode}",
                    file=sys.stderr,
                    flush=True,
                )
                _shutdown()
        time.sleep(1.0)


if __name__ == "__main__":
    raise SystemExit(main())
