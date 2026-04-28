#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
VENV_DIR="${VENV_DIR:-$PROJECT_ROOT/.venv}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
REQUIREMENTS_FILE="${REQUIREMENTS_FILE:-$PROJECT_ROOT/backend/requirements.txt}"
BASIC_PACKAGES=(
  pip
  setuptools
  wheel
)

usage() {
  cat <<'USAGE'
Usage:
  scripts/setup-python-env.sh [--all-worktrees] [--skip-playwright]

Environment variables:
  PYTHON_BIN          Python executable to use. Default: python3
  VENV_DIR            Virtualenv path. Default: <repo>/.venv
  REQUIREMENTS_FILE   Requirements file. Default: <repo>/backend/requirements.txt
  PNPM_BIN            pnpm executable to use. Default: pnpm, with npm exec fallback
  NPM_CACHE_DIR       npm cache for pnpm fallback. Default: /private/tmp/txt2img-npm-cache
  PLAYWRIGHT_BROWSERS_PATH
                      Browser install path. Default: <repo>/.playwright-browsers

Notes:
  --all-worktrees creates or updates a separate .venv inside each git worktree.
  The script recreates environments per worktree instead of copying .venv,
  because virtualenv entry points and config contain absolute paths.
  Playwright browser binaries are installed into <repo>/.playwright-browsers
  by default so Codex/Claude worktrees do not depend on the user cache.
USAGE
}

INSTALL_PLAYWRIGHT=1
SETUP_ALL_WORKTREES=0

setup_one() {
  local root="$1"
  local venv_dir="${VENV_DIR:-$root/.venv}"
  local requirements_file="${REQUIREMENTS_FILE:-$root/backend/requirements.txt}"
  local pnpm_bin="${PNPM_BIN:-pnpm}"
  local playwright_browsers_path="${PLAYWRIGHT_BROWSERS_PATH:-$root/.playwright-browsers}"

  if [[ ! -f "$requirements_file" ]]; then
    echo "Missing requirements file: $requirements_file" >&2
    return 1
  fi

  echo "Setting up Python environment in: $venv_dir"
  "$PYTHON_BIN" -m venv "$venv_dir"
  "$venv_dir/bin/python" -m pip install --upgrade "${BASIC_PACKAGES[@]}"
  "$venv_dir/bin/python" -m pip install -r "$requirements_file"

  if [[ "$INSTALL_PLAYWRIGHT" == "1" && -f "$root/frontend/package.json" ]]; then
    if ! command -v "$pnpm_bin" >/dev/null 2>&1 && ! command -v npm >/dev/null 2>&1; then
      echo "Missing pnpm and npm. Install pnpm, or install npm so this script can use npm exec pnpm." >&2
      return 1
    fi

    echo
    echo "Setting up frontend dependencies and Playwright in: $root/frontend"
    (
      cd "$root/frontend"
      run_pnpm "$pnpm_bin" install
      export PLAYWRIGHT_BROWSERS_PATH="$playwright_browsers_path"
      run_pnpm "$pnpm_bin" exec playwright install chromium
    )
  fi

  echo
  echo "Ready:"
  echo "  source $venv_dir/bin/activate"
  echo "  $venv_dir/bin/python -m pytest $root/backend/tests"
  if [[ -f "$root/frontend/package.json" ]]; then
    echo "  cd $root/frontend && $pnpm_bin run build"
    if [[ "$INSTALL_PLAYWRIGHT" == "1" ]]; then
      echo "  cd $root/frontend && PLAYWRIGHT_BROWSERS_PATH=$playwright_browsers_path $pnpm_bin exec playwright test"
    fi
  fi
}

run_pnpm() {
  local pnpm_bin="$1"
  shift

  if command -v "$pnpm_bin" >/dev/null 2>&1; then
    "$pnpm_bin" "$@"
  else
    npm --cache "${NPM_CACHE_DIR:-/private/tmp/txt2img-npm-cache}" exec --yes pnpm@latest -- "$@"
  fi
}

setup_all_worktrees() {
  git worktree list --porcelain |
    awk '/^worktree / { sub(/^worktree /, ""); print }' |
    while IFS= read -r worktree_root; do
      if [[ -d "$worktree_root" ]]; then
        (
          cd "$worktree_root"
          VENV_DIR="$worktree_root/.venv" REQUIREMENTS_FILE="$worktree_root/backend/requirements.txt" setup_one "$worktree_root"
        )
      fi
    done
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-playwright)
      INSTALL_PLAYWRIGHT=0
      shift
      ;;
    --all-worktrees)
      SETUP_ALL_WORKTREES=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "$SETUP_ALL_WORKTREES" == "1" ]]; then
  setup_all_worktrees
else
  setup_one "$PROJECT_ROOT"
fi
