#!/usr/bin/env bash
# One-time setup: a Python virtualenv for the backend and the frontend's packages.
# Re-running it is safe; it only fills in what is missing.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
NEW_ENOUGH='import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'

# Python 3.10 or newer: current FastAPI and Starlette need it, and the python3 that ships
# with macOS (3.9) no longer gets security fixes.
if ! "$PYTHON_BIN" -c "$NEW_ENOUGH" >/dev/null 2>&1; then
  echo "LocalDeck needs Python 3.10 or newer, and $PYTHON_BIN is $("$PYTHON_BIN" -V 2>&1)." >&2
  echo "Install one (python.org, Homebrew or uv), then run: PYTHON_BIN=python3.14 ./setup.sh" >&2
  exit 1
fi

# Backend. The venv is checked by whether its interpreter RUNS and is new enough, not by
# whether the folder exists: a venv whose base interpreter was removed still looks fine
# on disk, and one built on Python 3.9 cannot take the current requirements.
if ! "$ROOT_DIR/.venv/bin/python" -c "$NEW_ENOUGH" >/dev/null 2>&1; then
  rm -rf "$ROOT_DIR/.venv"
  "$PYTHON_BIN" -m venv "$ROOT_DIR/.venv"
fi
"$ROOT_DIR/.venv/bin/python" -m pip install --quiet --upgrade pip
"$ROOT_DIR/.venv/bin/python" -m pip install --quiet -r "$ROOT_DIR/backend/requirements.txt"

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
