#!/usr/bin/env bash
# Use the same local cache as setup.sh, outside the managed Documents workspace.
set -euo pipefail
cd "$(dirname "$0")/.."
project_id="$(printf '%s' "$PWD" | cksum | awk '{print $1}')"
runtime_root="${UR5E_RUNTIME_DIR:-${XDG_CACHE_HOME:-$HOME/Library/Caches}/ur5e-connector/$project_id}"
export UR5E_DATA_DIR="${UR5E_DATA_DIR:-$HOME/Library/Application Support/UR5eConnector/$project_id}"
export UV_PROJECT_ENVIRONMENT="$runtime_root/venv"
export PYTHONPYCACHEPREFIX="$runtime_root/pycache"
if [[ ! -x "$UV_PROJECT_ENVIRONMENT/bin/python" ]]; then
    echo "Run bash scripts/setup.sh before starting the connector." >&2
    exit 1
fi
exec uv run --locked --no-sync "$@"
