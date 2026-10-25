#!/usr/bin/env bash
# localdeck — single-command launcher.
#
# Usage:
#   ./launch.sh            Start localdeck (builds the UI if needed) and open it.
#   ./launch.sh --rebuild  Force a fresh frontend build before starting.
#
# Set LOCALDECK_NO_OPEN=1 to skip auto-opening the browser.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

PORT="${LOCALDECK_PORT:-8900}"
URL="http://127.0.0.1:${PORT}"
DIST_DIR="$ROOT_DIR/frontend/dist"
VITE_ENTRYPOINT="$ROOT_DIR/frontend/node_modules/vite/bin/vite.js"

# --- locate node -----------------------------------------------------------
NODE_BIN="${LOCALDECK_NODE_BIN:-}"
if [[ -z "$NODE_BIN" || ! -x "$NODE_BIN" ]]; then
  NODE_BIN="$(command -v node || true)"
fi

# --- one localdeck at a time ----------------------------------------------
# A second launch while one runs would rebuild the venv under the running server and then
# lose the port anyway. Open the running one instead.
if curl -s -m 2 "${URL}/api/health" >/dev/null 2>&1; then
  echo "Something is already serving ${URL} (localdeck, most likely); opening it."
  echo "To run a second copy, pick another port: LOCALDECK_PORT=8910 ./launch.sh"
  if [[ "${LOCALDECK_NO_OPEN:-0}" != "1" ]] && command -v open >/dev/null 2>&1; then open "$URL"; fi
  exit 0
fi

# --- setup: the first run, and a newer Python ------------------------------
# setup.sh also runs when the backend venv is not on the newest Python line it can use
# (scripts/pick_python.sh), so installing a newer Python moves localdeck onto it.
source "$ROOT_DIR/scripts/pick_python.sh"
PY_SKIP_FILE="$ROOT_DIR/.python-lines-tried"
WANT_PY="$(py_pick "$ROOT_DIR/backend/requirements.txt" || true)"
HAVE_LINE="$(py_line "$ROOT_DIR/.venv/bin/python")"
if [[ ! -x "$ROOT_DIR/.venv/bin/python" || ! -f "$VITE_ENTRYPOINT" ]] \
  || ! "$ROOT_DIR/.venv/bin/python" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1 \
  || [[ -n "$WANT_PY" && "$HAVE_LINE" != "$(py_line "$WANT_PY")" ]]; then
  echo "Setup: installing dependencies (backend Python ${HAVE_LINE:-none} -> $(py_line "$WANT_PY"))..."
  if [[ ! -x "$ROOT_DIR/setup.sh" ]]; then
    echo "Missing setup.sh. Cannot continue." >&2
    exit 1
  fi
  "$ROOT_DIR/setup.sh"
fi

if [[ ! -x "$ROOT_DIR/.venv/bin/python" ]]; then
  echo "Backend virtualenv still missing after setup. Run ./setup.sh manually." >&2
  exit 1
fi

# --- build frontend (only when needed) -------------------------------------
# Rebuild on --rebuild, when no build exists, or when any source file is newer
# than the last build (so UI edits are always picked up automatically).
NEED_BUILD=0
if [[ "${1:-}" == "--rebuild" ]]; then
  NEED_BUILD=1
elif [[ ! -f "$DIST_DIR/index.html" ]]; then
  NEED_BUILD=1
elif [[ -n "$(find "$ROOT_DIR/frontend/src" "$ROOT_DIR/frontend/index.html" \
              "$ROOT_DIR/frontend/vite.config.js" "$ROOT_DIR/frontend/package.json" \
              -newer "$DIST_DIR/index.html" -print -quit 2>/dev/null)" ]]; then
  NEED_BUILD=1
fi

if [[ "$NEED_BUILD" == "1" ]]; then
  if [[ -z "$NODE_BIN" || ! -x "$NODE_BIN" ]]; then
    echo "Unable to find Node.js to build the UI. Set LOCALDECK_NODE_BIN or install Node." >&2
    exit 1
  fi
  echo "Building the dashboard UI..."
  ( cd "$ROOT_DIR/frontend" && "$NODE_BIN" "$VITE_ENTRYPOINT" build )
fi

# --- open the browser once the server is healthy ---------------------------
if [[ "${LOCALDECK_NO_OPEN:-0}" != "1" ]]; then
  (
    for _ in {1..120}; do
      if curl -s "${URL}/api/health" >/dev/null 2>&1; then
        command -v open >/dev/null 2>&1 && open "$URL"
        break
      fi
      sleep 0.5
    done
  ) &
fi

# --- run the server (serves the built UI) ----------------------------------
export LOCALDECK_STATE_FILE="${LOCALDECK_STATE_FILE:-$ROOT_DIR/data/localdeck.json}"
echo "localdeck is starting on ${URL}  (press Ctrl+C to stop)"
exec "$ROOT_DIR/.venv/bin/python" -m uvicorn app.main:app \
  --app-dir "$ROOT_DIR/backend" --host 127.0.0.1 --port "$PORT"
