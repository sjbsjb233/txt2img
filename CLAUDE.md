# Claude Instructions

## Environment

- Use `scripts/setup-python-env.sh` from the repository root to prepare the local worktree.
- This installs a project-local Python runtime with `uv` under `.python/`, creates `.venv/` from that runtime, installs backend Python dependencies, installs frontend pnpm dependencies, and installs the Playwright Chromium browser into `.playwright-browsers/`.
- The default Python version is `3.14`, which resolves to the latest available 3.14 patch release. Override with `PYTHON_VERSION=3.x scripts/setup-python-env.sh`.
- If `pnpm` is not installed, the setup script falls back to `npm exec pnpm@latest` with a project-local npm cache under `.npm-cache/`.
- Use `scripts/setup-python-env.sh --all-worktrees` only when intentionally preparing every registered git worktree.
- Use `scripts/setup-python-env.sh --skip-playwright` when only backend Python work is needed.

## Commands

- Activate Python: `source .venv/bin/activate`
- Run backend tests: `.venv/bin/python -m pytest backend/tests`
- Build frontend: `cd frontend && pnpm run build`
- Install Playwright browser: `cd frontend && pnpm run playwright:install`
- Run Playwright tests: `cd frontend && pnpm run test:e2e`
- pnpm fallback: `cd frontend && npm --cache ../.npm-cache exec --yes pnpm@latest -- run build`

## Worktree Behavior

- Do not move or copy `.python/` or `.venv/` between worktrees. Create one runtime and environment per worktree.
- Codex and Claude worktrees may live outside the main repository path; the setup script handles this by resolving the current git root.
- Keep generated environment files, `.python/`, `.tools/`, `.uv-cache/`, `.pip-cache/`, `.npm-cache/`, `.playwright-browsers/`, Playwright reports, and test outputs out of git.
