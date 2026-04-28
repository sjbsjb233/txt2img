#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
VENV_DIR="${VENV_DIR:-$PROJECT_ROOT/.venv}"
PYTHON_VERSION="${PYTHON_VERSION:-3.14}"
TOOLS_DIR="${TOOLS_DIR:-$PROJECT_ROOT/.tools}"
UV_BIN="${UV_BIN:-$TOOLS_DIR/bin/uv}"
UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-$PROJECT_ROOT/.python}"
UV_CACHE_DIR="${UV_CACHE_DIR:-$PROJECT_ROOT/.uv-cache}"
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
  PYTHON_VERSION      Python version to install with uv. Default: 3.14
  UV_BIN              uv executable path. Default: <repo>/.tools/bin/uv
  UV_PYTHON_INSTALL_DIR
                      Python runtime install path. Default: <repo>/.python
  UV_CACHE_DIR        uv cache path. Default: <repo>/.uv-cache
  PIP_CACHE_DIR       pip cache path. Default: <repo>/.pip-cache
  VENV_DIR            Virtualenv path. Default: <repo>/.venv
  REQUIREMENTS_FILE   Requirements file. Default: <repo>/backend/requirements.txt
  PNPM_BIN            pnpm executable to use. Default: pnpm, with npm exec fallback
  NPM_CACHE_DIR       npm cache for pnpm fallback. Default: <repo>/.npm-cache
  PLAYWRIGHT_BROWSERS_PATH
                      Browser install path. Default: <repo>/.playwright-browsers

Notes:
  --all-worktrees creates or updates a separate Python runtime and .venv inside each git worktree.
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
  local npm_cache_dir="${NPM_CACHE_DIR:-$root/.npm-cache}"
  local playwright_browsers_path="${PLAYWRIGHT_BROWSERS_PATH:-$root/.playwright-browsers}"
  local tools_dir="${TOOLS_DIR:-$root/.tools}"
  local uv_bin="${UV_BIN:-$tools_dir/bin/uv}"
  local python_install_dir="${UV_PYTHON_INSTALL_DIR:-$root/.python}"
  local uv_cache_dir="${UV_CACHE_DIR:-$root/.uv-cache}"
  local pip_cache_dir="${PIP_CACHE_DIR:-$root/.pip-cache}"
  local python_bin

  if [[ ! -f "$requirements_file" ]]; then
    echo "Missing requirements file: $requirements_file" >&2
    return 1
  fi

  ensure_uv "$uv_bin"

  echo "Installing managed Python $PYTHON_VERSION into: $python_install_dir"
  UV_CACHE_DIR="$uv_cache_dir" UV_PYTHON_INSTALL_DIR="$python_install_dir" "$uv_bin" python install --no-bin --upgrade "$PYTHON_VERSION"
  python_bin="$(find_managed_python "$python_install_dir" "$PYTHON_VERSION")"

  echo "Setting up Python environment in: $venv_dir"
  "$python_bin" -m venv --clear "$venv_dir"
  PIP_CACHE_DIR="$pip_cache_dir" "$venv_dir/bin/python" -m pip install --upgrade "${BASIC_PACKAGES[@]}"
  PIP_CACHE_DIR="$pip_cache_dir" "$venv_dir/bin/python" -m pip install -r "$requirements_file"

  if [[ "$INSTALL_PLAYWRIGHT" == "1" && -f "$root/frontend/package.json" ]]; then
    if ! command -v "$pnpm_bin" >/dev/null 2>&1 && ! command -v npm >/dev/null 2>&1; then
      echo "Missing pnpm and npm. Install pnpm, or install npm so this script can use npm exec pnpm." >&2
      return 1
    fi

    echo
    echo "Setting up frontend dependencies and Playwright in: $root/frontend"
    (
      cd "$root/frontend"
      run_pnpm "$pnpm_bin" "$npm_cache_dir" install
      export PLAYWRIGHT_BROWSERS_PATH="$playwright_browsers_path"
      run_pnpm "$pnpm_bin" "$npm_cache_dir" exec playwright install chromium
    )
  fi

  echo
  echo "Ready:"
  echo "  python runtime: $python_bin"
  echo "  source $venv_dir/bin/activate"
  echo "  $venv_dir/bin/python -m pytest $root/backend/tests"
  if [[ -f "$root/frontend/package.json" ]]; then
    echo "  cd $root/frontend && $pnpm_bin run build"
    if [[ "$INSTALL_PLAYWRIGHT" == "1" ]]; then
      echo "  cd $root/frontend && PLAYWRIGHT_BROWSERS_PATH=$playwright_browsers_path $pnpm_bin exec playwright test"
    fi
  fi
}

find_managed_python() {
  local python_install_dir="$1"
  local python_version="$2"
  local python_bin_name="python${python_version}"
  local python_bin

  python_bin="$(find "$python_install_dir" -path "*/bin/$python_bin_name" -type f -perm -111 | sort | tail -n 1)"
  if [[ -z "$python_bin" ]]; then
    echo "Could not find managed $python_bin_name under $python_install_dir" >&2
    return 1
  fi

  echo "$python_bin"
}

ensure_uv() {
  local uv_bin="$1"
  local install_dir
  install_dir="$(dirname "$uv_bin")"

  if [[ -x "$uv_bin" ]]; then
    return 0
  fi

  mkdir -p "$install_dir"
  echo "Installing uv into: $install_dir"
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$install_dir" INSTALLER_NO_MODIFY_PATH=1 sh
}

run_pnpm() {
  local pnpm_bin="$1"
  local npm_cache_dir="$2"
  shift 2

  if command -v "$pnpm_bin" >/dev/null 2>&1; then
    "$pnpm_bin" "$@"
  else
    npm --cache "$npm_cache_dir" exec --yes pnpm@latest -- "$@"
  fi
}

setup_all_worktrees() {
  git worktree list --porcelain |
    awk '/^worktree / { sub(/^worktree /, ""); print }' |
    while IFS= read -r worktree_root; do
      if [[ -d "$worktree_root" ]]; then
        (
          cd "$worktree_root"
            TOOLS_DIR="$worktree_root/.tools" \
            UV_BIN="$worktree_root/.tools/bin/uv" \
            UV_PYTHON_INSTALL_DIR="$worktree_root/.python" \
            UV_CACHE_DIR="$worktree_root/.uv-cache" \
            PIP_CACHE_DIR="$worktree_root/.pip-cache" \
            NPM_CACHE_DIR="$worktree_root/.npm-cache" \
            VENV_DIR="$worktree_root/.venv" \
            REQUIREMENTS_FILE="$worktree_root/backend/requirements.txt" \
            setup_one "$worktree_root"
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
