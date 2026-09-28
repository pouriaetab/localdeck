#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

export LOCALDECK_STATE_FILE="$ROOT_DIR/data/localdeck.json"
exec "$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/scripts/dev.py"

