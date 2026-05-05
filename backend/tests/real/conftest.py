"""Real-upstream test fixtures.

Skipped automatically when ``backend/tests/real/.env.real`` is missing
or carries no usable keys.  Loads dotenv into the process env so the
tests can hand the values to the adapter / provider rows.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


ENV_FILE = Path(__file__).parent / ".env.real"


def _load_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


@pytest.fixture(scope="session", autouse=True)
def _ensure_real_env():
    if not ENV_FILE.exists():
        pytest.skip(
            "No tests/real/.env.real present — skipping real upstream tests.",
            allow_module_level=True,
        )
    env = _load_env_file(ENV_FILE)
    for k, v in env.items():
        os.environ.setdefault(k, v)
    if not os.environ.get("REAL_OPENAI_API_KEY") and not os.environ.get(
        "REAL_GEMINI_API_KEY"
    ):
        pytest.skip("Neither OPENAI nor GEMINI key present in .env.real.")
    yield
