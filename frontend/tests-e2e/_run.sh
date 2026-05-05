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
PORT_MOCK=18902
MOCK_LOG="${MOCK_LOG:-/tmp/mock_openai_upstream.jsonl}"

cleanup() {
  echo "--- cleanup ---"
  pkill -9 -f "uvicorn app.main:app" 2>/dev/null || true
  pkill -9 -f "vite.*--port ${PORT_FRONTEND}" 2>/dev/null || true
  pkill -9 -f "mock_openai_upstream" 2>/dev/null || true
  if [ "${KEEP_LOGS:-0}" = "1" ]; then
    cp -r "$DATA_DIR" /tmp/last-e2e-logs/ 2>/dev/null || true
    echo "logs preserved at /tmp/last-e2e-logs"
  else
    rm -rf "$DATA_DIR" || true
  fi
}
trap cleanup EXIT

# Take down any leftover processes from earlier runs first.
pkill -9 -f "uvicorn app.main.*:${PORT_BACKEND}" 2>/dev/null || true
pkill -9 -f "vite.*--port ${PORT_FRONTEND}" 2>/dev/null || true
pkill -9 -f "mock_openai_upstream" 2>/dev/null || true
sleep 1

echo "--- starting mock upstream ---"
MOCK_PORT="$PORT_MOCK" MOCK_LOG="$MOCK_LOG" \
  "$ROOT/.venv/bin/python" "$ROOT/frontend/tests-e2e/mock_openai_upstream.py" \
  > "$DATA_DIR/mock.log" 2>&1 &
for _ in $(seq 1 30); do
  if curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:${PORT_MOCK}/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.3
done
# /health isn't a route — accept any response (even 404) as proof the
# socket is bound.
if ! (echo > /dev/tcp/127.0.0.1/${PORT_MOCK}) 2>/dev/null; then
  echo "mock upstream did not bind; log:" >&2
  cat "$DATA_DIR/mock.log" >&2
  exit 1
fi
echo "mock upstream up on $PORT_MOCK (log: $MOCK_LOG)"

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
  --log-level info \
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
E2E_BACKEND_URL="http://127.0.0.1:${PORT_BACKEND}" \
MOCK_BASE="http://127.0.0.1:${PORT_MOCK}/v1" \
MOCK_LOG="$MOCK_LOG" \
  npm --cache "$ROOT/.npm-cache" exec --yes pnpm@latest -- exec playwright test
