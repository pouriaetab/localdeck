#!/usr/bin/env bash
# One-time setup: a Python virtualenv for the backend and the frontend's packages.
# Re-running it is safe; it only fills in what is missing.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"

# Backend. The venv is checked by whether its interpreter RUNS, not by whether the
# folder exists: a venv whose base interpreter was removed still looks fine on disk.
if ! "$ROOT_DIR/.venv/bin/python" -c "import sys" >/dev/null 2>&1; then
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
