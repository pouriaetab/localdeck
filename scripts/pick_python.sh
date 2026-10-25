# pick_python.sh: which Python the backend runs on. Sourced by setup.sh and launch.sh (bash or zsh).
#
# The newest line in PY_LINES that is installed on this machine and that the backend's packages install on. A venv
# on an older line is moved aside (never deleted: .venv-py3.9-<when>) and a new one is built. If the packages do not
# install on the newer line, the old venv comes back, and that line is noted in .python-lines-tried until
# requirements.txt changes.
#
# Where an interpreter comes from, best first: python.org's installer (its venvs survive patch updates), uv's own
# Pythons, then Homebrew's and PATH. PYTHON_BIN=/path/to/python3.x overrides all of it.
# A new Python line comes out every October: it goes first in PY_LINES once CI passes on it.
PY_LINES=(3.15 3.14 3.13 3.12 3.11 3.10)

py_line() {   # X.Y of an interpreter (or a venv's python); prints nothing when it does not run
  if [ -n "${1:-}" ] && [ -x "$1" ]; then
    "$1" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true
  fi
}

py_find_line() {   # an interpreter for one line, or nothing. PY_ONLY_PATH=1 looks on PATH only (tests).
  local line="$1" c
  if [ "${PY_ONLY_PATH:-0}" != 1 ]; then
    for c in "/Library/Frameworks/Python.framework/Versions/$line/bin/python$line" \
             "$(command -v uv >/dev/null 2>&1 && uv python find --managed-python "$line" 2>/dev/null || true)" \
             "/opt/homebrew/bin/python$line" "/usr/local/bin/python$line"; do
      if [ -n "$c" ] && [ "$(py_line "$c")" = "$line" ]; then
        echo "$c"
        return 0
      fi
    done
  fi
  c="$(command -v "python$line" 2>/dev/null || true)"
  if [ -n "$c" ] && [ "$(py_line "$c")" = "$line" ]; then
    echo "$c"
    return 0
  fi
  return 1
}

py_reqs_sum() {   # changes when the requirements change, so a line that failed gets another try then
  cat "$@" 2>/dev/null | cksum | cut -d' ' -f1
}

py_tried() {   # py_tried LINE REQ_FILE...: did this line already fail with these requirements?
  local line="$1"; shift
  [ -f "${PY_SKIP_FILE:-.python-lines-tried}" ] \
    && grep -qx "$line $(py_reqs_sum "$@")" "${PY_SKIP_FILE:-.python-lines-tried}"
}

py_pick() {   # py_pick REQ_FILE...: the interpreter to build the venv with
  local line c
  if [ -n "${PYTHON_BIN:-}" ]; then
    echo "$PYTHON_BIN"
    return 0
  fi
  for line in "${PY_LINES[@]}"; do
    py_tried "$line" "$@" && continue
    if c="$(py_find_line "$line")"; then
      echo "$c"
      return 0
    fi
  done
  return 1
}

# py_venv VENV REQ_FILE...: VENV ends up on the picked line with the requirements installed. A venv on another
# line is moved aside, never deleted (VENV-py3.11-<when>), and a new one is built. If the packages do not install
# on the new line, the new one is moved aside too (VENV-failed-py3.15-<when>), the line is written to the skip
# file, the old venv comes back and the next line is tried. Nothing happens when VENV is already on the picked line.
py_venv() {
  local venv="$1"; shift
  local want line have old stamp f
  local -a reqs
  reqs=()
  for f in "$@"; do reqs+=(-r "$f"); done
  stamp="$(date +%Y%m%d-%H%M%S)"
  while want="$(py_pick "$@")"; do
    line="$(py_line "$want")"
    have="$(py_line "$venv/bin/python")"
    [ "$have" = "$line" ] && return 0
    old=""
    if [ -e "$venv" ] || [ -L "$venv" ]; then
      old="$venv-py${have:-broken}-$stamp"
      mv "$venv" "$old"
    fi
    echo "[python] building $venv on Python $("$want" -V 2>&1 | cut -d' ' -f2) ($want)" >&2
    if "$want" -m venv "$venv" \
       && "$venv/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade pip \
       && "$venv/bin/python" -m pip install --quiet --disable-pip-version-check "${reqs[@]}"; then
      [ -n "$old" ] && echo "[python] the old one (Python ${have:-?}) is kept at $old" >&2
      return 0
    fi
    echo "[python] the packages did not install on Python $line; noted in ${PY_SKIP_FILE:-.python-lines-tried}" >&2
    if [ -e "$venv" ]; then mv "$venv" "$venv-failed-py$line-$stamp"; fi
    echo "$line $(py_reqs_sum "$@")" >> "${PY_SKIP_FILE:-.python-lines-tried}"
    if [ -n "$old" ]; then mv "$old" "$venv"; fi
  done
  return 1
}
