# Claude Instructions

## Environment

- Use `scripts/setup-python-env.sh` from the repository root to prepare the local worktree.
- This creates `.venv/`, installs backend Python dependencies, installs frontend pnpm dependencies, and installs the Playwright Chromium browser into `.playwright-browsers/`.
- If `pnpm` is not installed, the setup script falls back to `npm exec pnpm@latest` with a temporary npm cache under `/private/tmp/txt2img-npm-cache`.
- Use `scripts/setup-python-env.sh --all-worktrees` only when intentionally preparing every registered git worktree.
- Use `scripts/setup-python-env.sh --skip-playwright` when only backend Python work is needed.

## Commands

- Activate Python: `source .venv/bin/activate`
- Run backend tests: `.venv/bin/python -m pytest backend/tests`
- Build frontend: `cd frontend && pnpm run build`
- Install Playwright browser: `cd frontend && pnpm run playwright:install`
- Run Playwright tests: `cd frontend && pnpm run test:e2e`
- pnpm fallback: `cd frontend && npm --cache /private/tmp/txt2img-npm-cache exec --yes pnpm@latest -- run build`

## Worktree Behavior

- Do not move or copy `.venv/` between worktrees. Create one environment per worktree.
- Codex and Claude worktrees may live outside the main repository path; the setup script handles this by resolving the current git root.
- Keep generated environment files, `.playwright-browsers/`, Playwright reports, and test outputs out of git.
