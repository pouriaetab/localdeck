#!/bin/bash
# Tests for scripts/pick_python.sh under bash and zsh (zsh is skipped when it is not installed).
# Fake interpreters stand in for real Pythons: no network, no real venvs. Run: bash scripts/test_pick_python.sh
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
PICK="$HERE/pick_python.sh"
pass=0; fail=0
ok() { if [ "$1" = 0 ]; then pass=$((pass+1)); echo "  ok    $2"; else fail=$((fail+1)); echo "  FAIL  $2"; fi; }

make_fake() {   # make_fake DIR LINE: a fake python that answers -c, -V, -m venv and -m pip
  mkdir -p "$1"
  cat > "$1/python$2" <<'PY'
#!/bin/bash
LINE=__LINE__
case "$1" in
  -c) echo "$LINE" ;;
  -V) echo "Python $LINE.0" ;;
  -m)
    case "$2" in
      venv) mkdir -p "$3/bin"; sed "s#^LINE=.*#LINE=$LINE#" "$0" > "$3/bin/python"; chmod +x "$3/bin/python" ;;
      pip) if [[ " ${FAIL_LINES:-} " == *" $LINE "* && "$*" == *" -r "* ]]; then
             # PIP_LOG counts the tries; after 3 it lets one through, so a retry loop shows up as a failed check, not a hang
             if [ -n "${PIP_LOG:-}" ]; then echo x >> "$PIP_LOG"; [ "$(wc -l < "$PIP_LOG")" -gt 3 ] && exit 0; fi
             echo "no wheel for $LINE" >&2; exit 1
           fi ;;
    esac ;;
esac
PY
  sed -i.bak "s#__LINE__#$2#" "$1/python$2" && rm -f "$1/python$2.bak"
  chmod +x "$1/python$2"
}

for SH in bash zsh; do
  if ! command -v "$SH" >/dev/null 2>&1; then echo "== $SH (not installed, skipped)"; continue; fi
  echo "== $SH"
  T="$(mktemp -d)"; mkdir -p "$T/bin" "$T/proj"
  make_fake "$T/bin" 3.15; make_fake "$T/bin" 3.14; make_fake "$T/old" 3.9
  echo "fastapi" > "$T/proj/req.txt"
  # PY_ONLY_PATH=1: the machine's own Pythons (a macOS CI runner has several in /Library/Frameworks and
  # /opt/homebrew) must never stand in for the fakes.
  run() {   # run 'shell code' in a fresh shell, strict mode, with only the fakes on PATH
    env -i HOME="$T" PATH="$T/bin:/usr/bin:/bin" PY_ONLY_PATH=1 FAIL_LINES="${FAIL_LINES:-}" PYTHON_BIN="${PYTHON_BIN:-}" \
      PIP_LOG="${PIP_LOG:-}" \
      "$SH" -c "set -euo pipefail; cd '$T/proj'; source '$PICK'; PY_LINES=(3.15 3.14); PY_SKIP_FILE='$T/proj/.tried'; $1" 2>"$T/err"
  }
  "$T/old/python3.9" -m venv "$T/proj/.venv"
  FAIL_LINES="" run 'py_venv .venv req.txt'; rc=$?
  [ $rc = 0 ] && [ "$("$T/proj/.venv/bin/python" -c x)" = 3.15 ] && ls -d "$T"/proj/.venv-py3.9-* >/dev/null 2>&1
  ok $? "a 3.9 venv moves aside and a 3.15 one is built"
  rm -rf "$T"/proj/.venv* "$T/proj/.tried"
  "$T/old/python3.9" -m venv "$T/proj/.venv"
  FAIL_LINES="3.15" run 'py_venv .venv req.txt'; rc=$?
  [ $rc = 0 ] && [ "$("$T/proj/.venv/bin/python" -c x)" = 3.14 ] && ls -d "$T"/proj/.venv-failed-py3.15-* >/dev/null 2>&1 \
    && grep -q '^3.15 ' "$T/proj/.tried" && ls -d "$T"/proj/.venv-py3.9-* >/dev/null 2>&1
  ok $? "the packages fail on 3.15: 3.14 is used, 3.15 is noted, both earlier venvs are kept"
  before="$(ls -a "$T/proj" | tr '\n' ' ')"
  FAIL_LINES="3.15" run 'py_venv .venv req.txt'; rc=$?
  [ $rc = 0 ] && [ "$before" = "$(ls -a "$T/proj" | tr '\n' ' ')" ]
  ok $? "the next start does not try 3.15 again and moves nothing"
  echo "uvicorn" >> "$T/proj/req.txt"
  FAIL_LINES="" run 'py_venv .venv req.txt'; rc=$?
  [ $rc = 0 ] && [ "$("$T/proj/.venv/bin/python" -c x)" = 3.15 ]
  ok $? "new requirements: 3.15 gets another try and takes over"
  out="$(PYTHON_BIN="$T/bin/python3.14" run 'py_pick req.txt')"
  [ "$out" = "$T/bin/python3.14" ]
  ok $? "PYTHON_BIN wins"
  rm -rf "$T"/proj/.venv* "$T/proj/.tried"
  "$T/old/python3.9" -m venv "$T/proj/.venv"
  FAIL_LINES="3.15 3.14" run 'py_venv .venv req.txt'; rc=$?
  [ $rc = 1 ] && [ "$("$T/proj/.venv/bin/python" -c x)" = 3.9 ]
  ok $? "nothing installs: py_venv says so and the old venv is back"
  rm -rf "$T"/proj/.venv* "$T/proj/.tried"
  FAIL_LINES="" run 'py_venv .venv req.txt'; rc=$?
  [ $rc = 0 ] && [ "$("$T/proj/.venv/bin/python" -c x)" = 3.15 ] && [ -z "$(ls -d "$T"/proj/.venv-* 2>/dev/null)" ]
  ok $? "no venv yet: one is built on the newest line"
  rm -rf "$T"/proj/.venv* "$T/proj/.tried"
  "$T/old/python3.9" -m venv "$T/proj/.venv"
  : > "$T/pip.log"
  FAIL_LINES="3.15" PYTHON_BIN="$T/bin/python3.15" PIP_LOG="$T/pip.log" run 'py_venv .venv req.txt'; rc=$?
  [ $rc = 1 ] && [ "$(wc -l < "$T/pip.log" | tr -d ' ')" = 1 ] && [ "$("$T/proj/.venv/bin/python" -c x)" = 3.9 ]
  ok $? "PYTHON_BIN whose packages fail: one try, then it says so and the old venv is back (no endless retry)"
  out="$(run 'echo "[$(py_line ./nothing)]"')"
  [ "$out" = "[]" ]
  ok $? "py_line of a missing interpreter is empty, and strict mode survives it"
  rm -rf "$T"
done
echo "$pass checks passed, $fail failed"
[ "$fail" = 0 ]
