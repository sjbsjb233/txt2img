# Agent Instructions

## Project Setup

- Run `scripts/setup-python-env.sh` from the repository root before Python or browser-based work.
- The script creates a local `.venv/`, installs `backend/requirements.txt`, installs frontend dependencies with pnpm, and installs the Playwright Chromium browser into `.playwright-browsers/`.
- If `pnpm` is not installed, the setup script falls back to `npm exec pnpm@latest` with a temporary npm cache under `/private/tmp/txt2img-npm-cache`.
- For every registered git worktree, run `scripts/setup-python-env.sh --all-worktrees` from the main worktree.
- Do not copy `.venv/` between worktrees. Virtualenv files contain absolute paths, so each worktree needs its own environment.
- If browser setup is unnecessary, use `scripts/setup-python-env.sh --skip-playwright`.

## Common Commands

- Activate Python: `source .venv/bin/activate`
- Backend tests: `.venv/bin/python -m pytest backend/tests`
- Frontend install/build: `cd frontend && pnpm install && pnpm run build`
- Playwright browser install: `cd frontend && pnpm run playwright:install`
- Playwright tests: `cd frontend && pnpm run test:e2e`
- pnpm fallback: `cd frontend && npm --cache /private/tmp/txt2img-npm-cache exec --yes pnpm@latest -- run build`

## Worktree Notes

- Codex worktrees are often created outside the main repository directory, such as under `~/.codex/worktrees`.
- The setup script uses `git rev-parse --show-toplevel`, so it works from any checked-out worktree location.
- `.venv/`, `.playwright-browsers/`, Playwright reports, and test results are ignored by git and should stay local to each worktree.
