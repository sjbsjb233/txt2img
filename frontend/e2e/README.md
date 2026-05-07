# Picker E2E tests

Playwright-driven smoke + verification suite for the `/picker` route.

## Prerequisites

- Backend running at `http://127.0.0.1:18000`
- Frontend dev server running at `http://localhost:5173`
- `e2e_picker_user` user created in the DB
- `pnpm run playwright:install` run once

## How to run

From the repo root:

```bash
# 1. Boot backend (with the test env)
set -a; source backend.env; set +a
.venv/bin/python -m uvicorn app.main:app \
  --app-dir backend --host 127.0.0.1 --port 18000 &

# 2. Boot frontend dev server
(cd frontend && VITE_API_BASE=http://127.0.0.1:18000 pnpm run dev) &

# 3. Run Playwright
cd frontend && pnpm run test:e2e
```

The seeder (`e2e/scripts/seed_picker_data.py`) is invoked automatically
in `beforeAll` — it resets the test user's data and primes a deck with
five sessions covering every picker state.

## Files

```
e2e/
├── fixtures/auth.js               login + storageState helpers
├── scripts/seed_picker_data.py    direct-DB seeder (bypasses providers)
└── picker/00-smoke.spec.js        eight-test smoke covering all five boards
```

## Test IDs

The picker components carry `data-testid` hooks for stable selection:

- `picker-deck-overview` — entry page root
- `picker-judging-page` — main judging surface
- `picker-sessions-button` / `picker-fullscreen-button`
- `picker-sessions-drawer[data-open=true|false]`
- `picker-fullscreen` — fullscreen container
- `picker-finalize-confirm-modal` — confirm-swap modal
- `picker-session-card-<id>` — deck overview card

Always select via testid, not by visible text — text changes shouldn't
break tests.

## Output

- HTML report: `playwright-report/`
- Smoke screenshots: `playwright-report/shots/01-deck-overview.png` etc.
- On failure: trace + video in `frontend/test-results/`
