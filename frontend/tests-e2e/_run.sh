#!/bin/bash
# Boot backend + frontend + seed + run Playwright e2e end-to-end.
#
# Idempotent: kills anything left over from a prior run, exits non-zero
# if the test suite fails, prints log paths on failure for triage.

set -euo pipefail

ROOT="$( cd "$( dirname "${BASH_SOURCE[0]}" )/../.." && pwd )"
cd "$ROOT"

DATA_DIR=$(mktemp -d /tmp/feat-thinking-size-e2e.XXXXXX)
DB_PATH="$DATA_DIR/e2e.db"

PORT_BACKEND=18901
PORT_FRONTEND=5174

cleanup() {
  echo "--- cleanup ---"
  pkill -9 -f "uvicorn app.main.*:${PORT_BACKEND}" 2>/dev/null || true
  pkill -9 -f "vite.*--port ${PORT_FRONTEND}" 2>/dev/null || true
  rm -rf "$DATA_DIR" || true
}
trap cleanup EXIT

# Take down any leftover processes from earlier runs first.
pkill -9 -f "uvicorn app.main.*:${PORT_BACKEND}" 2>/dev/null || true
pkill -9 -f "vite.*--port ${PORT_FRONTEND}" 2>/dev/null || true
sleep 1

echo "--- starting backend ---"
JWT_SECRET="e2e_thinking_size_secret_at_least_16_chars" \
ADMIN_USERNAME="admin" \
ADMIN_PASSWORD="test-admin-password" \
DB_URL="sqlite+aiosqlite:///${DB_PATH}" \
DATA_ROOT="${DATA_DIR}/data" \
CORS_ORIGINS="http://127.0.0.1:${PORT_FRONTEND},http://localhost:${PORT_FRONTEND}" \
"$ROOT/.venv/bin/python" -m uvicorn app.main:app \
  --app-dir "$ROOT/backend" \
  --host 127.0.0.1 --port "$PORT_BACKEND" \
  --log-level warning \
  > "$DATA_DIR/backend.log" 2>&1 &

# Wait for /api/health
for _ in $(seq 1 40); do
  if curl -s "http://127.0.0.1:${PORT_BACKEND}/api/health" > /dev/null 2>&1; then
    break
  fi
  sleep 0.5
done
if ! curl -s "http://127.0.0.1:${PORT_BACKEND}/api/health" > /dev/null 2>&1; then
  echo "backend never came up; log:" >&2
  cat "$DATA_DIR/backend.log" >&2
  exit 1
fi
echo "backend up on $PORT_BACKEND"

echo "--- seeding e2e provider ---"
E2E_BACKEND_URL="http://127.0.0.1:${PORT_BACKEND}" \
ADMIN_PASSWORD="test-admin-password" \
"$ROOT/.venv/bin/python" "$ROOT/frontend/tests-e2e/seed_e2e_provider.py"

echo "--- starting frontend dev ---"
cd "$ROOT/frontend"
VITE_API_BASE="http://127.0.0.1:${PORT_BACKEND}" \
  npm --cache "$ROOT/.npm-cache" exec --yes pnpm@latest -- run dev \
  --host 127.0.0.1 --port "$PORT_FRONTEND" \
  > "$DATA_DIR/frontend.log" 2>&1 &

# Wait for vite
for _ in $(seq 1 60); do
  if curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:${PORT_FRONTEND}" | grep -q "200"; then
    break
  fi
  sleep 0.5
done
if ! curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:${PORT_FRONTEND}" | grep -q "200"; then
  echo "frontend never came up; log:" >&2
  cat "$DATA_DIR/frontend.log" >&2
  exit 1
fi
echo "frontend up on $PORT_FRONTEND"

echo "--- running playwright ---"
PLAYWRIGHT_BROWSERS_PATH="$ROOT/.playwright-browsers" \
E2E_BASE_URL="http://127.0.0.1:${PORT_FRONTEND}" \
  npm --cache "$ROOT/.npm-cache" exec --yes pnpm@latest -- exec playwright test
