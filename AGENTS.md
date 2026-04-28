# Agent Instructions

## Project Setup

- Run `scripts/setup-python-env.sh` from the repository root before Python or browser-based work.
- The script installs a project-local Python runtime with `uv` under `.python/`, creates `.venv/` from that runtime, installs `backend/requirements.txt`, installs frontend dependencies with pnpm, and installs the Playwright Chromium browser into `.playwright-browsers/`.
- The default Python version is `3.14`, which resolves to the latest available 3.14 patch release. Override with `PYTHON_VERSION=3.x scripts/setup-python-env.sh`.
- If `pnpm` is not installed, the setup script falls back to `npm exec pnpm@latest` with a project-local npm cache under `.npm-cache/`.
- For every registered git worktree, run `scripts/setup-python-env.sh --all-worktrees` from the main worktree.
- Do not copy `.python/` or `.venv/` between worktrees. Both contain absolute paths, so each worktree needs its own runtime and environment.
- If browser setup is unnecessary, use `scripts/setup-python-env.sh --skip-playwright`.

## Common Commands

- Activate Python: `source .venv/bin/activate`
- Backend tests: `.venv/bin/python -m pytest backend/tests`
- Frontend install/build: `cd frontend && pnpm install && pnpm run build`
- Playwright browser install: `cd frontend && pnpm run playwright:install`
- Playwright tests: `cd frontend && pnpm run test:e2e`
- pnpm fallback: `cd frontend && npm --cache ../.npm-cache exec --yes pnpm@latest -- run build`

## Worktree Notes

- Codex worktrees are often created outside the main repository directory, such as under `~/.codex/worktrees`.
- The setup script uses `git rev-parse --show-toplevel`, so it works from any checked-out worktree location.
- `.python/`, `.tools/`, `.uv-cache/`, `.pip-cache/`, `.npm-cache/`, `.venv/`, `.playwright-browsers/`, Playwright reports, and test results are ignored by git and should stay local to each worktree.
