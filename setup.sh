#!/usr/bin/env bash
# One-time setup: a Python virtualenv for the backend and the frontend's packages.
# Re-running it is safe; it only fills in what is missing.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

NEW_ENOUGH='import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'

# Backend. It runs on the newest Python line installed here that its packages install on
# (scripts/pick_python.sh; PYTHON_BIN=/path/to/python3.x picks one by hand), so it moves up
# when a newer Python is installed. The python3 that ships with macOS (3.9) is never used:
# current FastAPI and Starlette need 3.10 or newer, and 3.9 no longer gets security fixes.
# A venv on an older line is moved aside (.venv-py3.9-<when>), never deleted.
source "$ROOT_DIR/scripts/pick_python.sh"
PY_SKIP_FILE="$ROOT_DIR/.python-lines-tried"
REQS="$ROOT_DIR/backend/requirements.txt"
if ! py_venv "$ROOT_DIR/.venv" "$REQS"; then
  if "$ROOT_DIR/.venv/bin/python" -c "$NEW_ENOUGH" >/dev/null 2>&1; then
    echo "Keeping the backend venv on Python $(py_line "$ROOT_DIR/.venv/bin/python"): the packages did not install on a newer one." >&2
  else
    echo "LocalDeck needs Python 3.10 or newer. Install one (python.org, Homebrew or uv) and run ./setup.sh again," >&2
    echo "or pick one by hand: PYTHON_BIN=/path/to/python3.14 ./setup.sh" >&2
    exit 1
  fi
fi
"$ROOT_DIR/.venv/bin/python" -m pip install --quiet --upgrade pip
"$ROOT_DIR/.venv/bin/python" -m pip install --quiet -r "$REQS"

# Frontend. pnpm if present (the lockfile is pnpm's), npm otherwise.
if command -v pnpm >/dev/null 2>&1; then
  pnpm --dir "$ROOT_DIR/frontend" install
elif command -v npm >/dev/null 2>&1; then
  npm --prefix "$ROOT_DIR/frontend" install
else
  echo "Node.js is required for the dashboard UI. Install Node 18+ and re-run ./setup.sh." >&2
  exit 1
fi

# Verify the artefacts, not the exit codes: the build needs vite and its native esbuild.
( cd "$ROOT_DIR/frontend" && node -e "const v = require.resolve('vite'); require(require.resolve('esbuild', { paths: [v] }))" ) || {
  echo "Frontend packages did not install completely. Re-run ./setup.sh." >&2
  exit 1
}

# data/localdeck.json is created on first start, seeded with the demo projects in
# examples/. It is yours and is never committed (see .gitignore).

echo "Setup complete. Start with ./launch.sh"
