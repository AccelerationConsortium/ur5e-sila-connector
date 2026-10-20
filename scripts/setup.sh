#!/usr/bin/env bash
# Keep dependency files and bytecode out of the managed Documents workspace.
set -euo pipefail
cd "$(dirname "$0")/.."
project_id="$(printf '%s' "$PWD" | cksum | awk '{print $1}')"
runtime_root="${UR5E_RUNTIME_DIR:-${XDG_CACHE_HOME:-$HOME/Library/Caches}/ur5e-connector/$project_id}"
export UV_PROJECT_ENVIRONMENT="$runtime_root/venv"
export PYTHONPYCACHEPREFIX="$runtime_root/pycache"
uv sync --locked --all-extras --link-mode copy "$@"
uv run --no-sync python -c 'import unitelabs.ur5e_connector; print("UR5e connector environment ready")'
