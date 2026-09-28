"""Portable project rules — the ones that generalise across every project here.

WHY THIS FILE EXISTS
--------------------
Every rule below traces to a failure that actually happened, not to a style opinion.
The test is deliberately strict: a rule earns a place here only if it is
*context-free* — true for any project regardless of what the software does. Anything
domain-specific (a wrong statistical estimator, a provider's field names) belongs in a
regression test in that project's own repo, NOT here. Roughly half of real bugs are
domain-specific, and pretending otherwise is how a shared rule set rots into a ritual.

Every check is defensive: a bug in a checker must never take localdeck down, so
each returns a WARN rather than raising.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Dict, List

FAIL, WARN, PASS = "fail", "warn", "pass"

SKIP_DIRS = {".git", "node_modules", "dist", "build", "__pycache__", ".pytest_cache",
             ".mypy_cache", ".ruff_cache", "data", "venv", "env"}

# Packages that ship COMPILED artefacts. Installing one on an interpreter with no wheel
# silently falls back to building from source: minutes of compiling, a toolchain
# requirement, and usually a failure that looks like a hang.
COMPILED_PACKAGES = {
    "numpy", "scipy", "pandas", "pydantic", "pydantic-core", "lxml", "cryptography",
    "pillow", "psycopg2", "psycopg2-binary", "matplotlib", "pyarrow", "grpcio",
    "scikit-learn", "statsmodels", "orjson", "ujson", "watchfiles", "httptools",
}

SECRET_PATTERNS = [
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}"), "OpenAI-style API key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key id"),
    (re.compile(r"\bghp_[A-Za-z0-9]{30,}"), "GitHub personal access token"),
    (re.compile(r"(?i)\b(password|passwd|secret|api_?key|token)\s*=\s*[\"'][^\"'\s]{8,}[\"']"),
     "hardcoded credential"),
]

CODE_EXTS = {".py", ".js", ".jsx", ".ts", ".tsx", ".sh"}


# --------------------------------------------------------------------------- helpers

def _check(cid: str, title: str, status: str, detail: str, suggestion: str = "") -> Dict[str, Any]:
    return {"id": cid, "title": title, "status": status, "detail": detail,
            "suggestion": suggestion}


def _walk(root: Path, max_depth: int = 4) -> List[Path]:
    out: List[Path] = []
    def rec(d: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            entries = list(d.iterdir())
        except OSError:
            return
        for e in entries:
            if e.name in SKIP_DIRS or e.name.startswith(".venv"):
                continue
            if e.is_dir():
                rec(e, depth + 1)
            else:
                out.append(e)
    rec(root, 0)
    return out


def _read(p: Path, limit: int = 400_000) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="ignore")[:limit]
    except OSError:
        return ""


def _requirements(root: Path) -> List[Path]:
    return [p for p in root.rglob("requirements*.txt")
            if not any(part in SKIP_DIRS or part.startswith(".venv") for part in p.parts)]


def _declared_packages(root: Path) -> Dict[str, Path]:
    pkgs: Dict[str, Path] = {}
    for req in _requirements(root):
        for line in _read(req).splitlines():
            line = line.strip()
            if not line or line.startswith(("#", "-")):
                continue
            name = re.split(r"[=<>!~\[; ]", line, 1)[0].strip().lower()
            if name:
                pkgs[name] = req
    return pkgs


# ---------------------------------------------------------------------------- rules

def rule_interpreter_pin(root: Path) -> Dict[str, Any]:
    """INCIDENT 2026-09-08: a project pinned numpy and pydantic while the machine's
    default python3 was 3.14, for which neither publishes a wheel. pip fell back to
    compiling from source and the launcher showed a spinner forever."""
    declared = _declared_packages(root)
    compiled = sorted(set(declared) & COMPILED_PACKAGES)
    if not compiled:
        return _check("interpreter_pin", "Interpreter is pinned", PASS,
                      "No compiled dependencies declared, so interpreter drift is low risk.")
    evidence = []
    for name in ("run.sh", ".python-version", "pyproject.toml", "Makefile"):
        txt = _read(root / name)
        if re.search(r"SUPPORTED_PY|requires-python|python3\.\d+|3\.\d+", txt):
            evidence.append(name)
    if evidence:
        return _check("interpreter_pin", "Interpreter is pinned", PASS,
                      f"Compiled deps ({', '.join(compiled[:4])}) with an interpreter "
                      f"constraint in {', '.join(evidence)}.")
    return _check(
        "interpreter_pin", "Interpreter is pinned", FAIL,
        f"Compiled dependencies ({', '.join(compiled[:5])}) with no interpreter version "
        "pinned anywhere. On a Python with no matching wheel, pip builds from source: "
        "minutes of compiling that looks exactly like a hang.",
        "Declare the supported versions (a SUPPORTED_PY list in run.sh, a "
        ".python-version file, or requires-python in pyproject.toml), refuse to run on "
        "anything else, and install with --only-binary=:all: so a missing wheel fails "
        "fast instead of compiling.")


def rule_venv_not_shared(root: Path) -> Dict[str, Any]:
    """INCIDENT 2026-09-07: one project folder opened from a macOS desktop and a Linux
    VM shared a single .venv. Each run judged the other's venv broken, deleted it and
    reinstalled - which read as a hang."""
    generic = [p for p in root.rglob(".venv") if p.is_dir()]
    per_platform = [p for p in root.rglob(".venv-*") if p.is_dir()]
    if generic and per_platform:
        return _check("venv_shared", "Virtualenv is per-platform", WARN,
                      "Both a shared .venv and per-platform venvs exist.",
                      "Delete the legacy .venv so it cannot be picked up by mistake.")
    if generic:
        return _check(
            "venv_shared", "Virtualenv is per-platform", WARN,
            "A single .venv is used. If this folder is ever opened from a second "
            "machine or OS they will clobber each other on every run.",
            "Suffix the venv path with the platform, e.g. .venv-$(uname -s)-$(uname -m), "
            "and add .venv* to .gitignore.")
    return _check("venv_not_shared", "Virtualenv is per-platform", PASS,
                  "No shared .venv found." if not per_platform else
                  f"{len(per_platform)} per-platform venv(s).")


def rule_venv_complete(root: Path) -> Dict[str, Any]:
    """INCIDENT 2026-09-08: an interrupted install left a venv containing only pip. It
    looked installed, never worked, and was retried on every start."""
    venvs = [p for p in list(root.rglob(".venv")) + list(root.rglob(".venv-*")) if p.is_dir()]
    broken = []
    for v in venvs:
        sp = list(v.glob("lib/python*/site-packages"))
        if not sp:
            broken.append(v.name)
            continue
        installed = {d.name for d in sp[0].iterdir()} if sp[0].is_dir() else set()
        if len(installed - {"pip", "setuptools", "wheel", "pkg_resources", "_distutils_hack"}) < 3:
            broken.append(v.name)
    if broken:
        return _check(
            "venv_complete", "Virtualenv installs completed", WARN,
            f"Venv(s) present but essentially empty: {', '.join(broken)}. An "
            "interrupted install leaves an environment that looks ready and never works.",
            "Write a marker file (e.g. .deps-ok) only after the install succeeds, and "
            "treat a venv without it as a failed install to be rebuilt.")
    return _check("venv_complete", "Virtualenv installs completed", PASS,
                  "No half-installed virtualenv found.")


def rule_unused_dependencies(root: Path) -> Dict[str, Any]:
    """INCIDENT 2026-09-08: pandas was pinned and never imported once. It was a large
    part of a cold install that was already too slow."""
    declared = _declared_packages(root)
    if not declared:
        return _check("unused_deps", "No unused dependencies", PASS,
                      "No requirements file found.")
    source = "\n".join(_read(p) for p in _walk(root) if p.suffix == ".py")
    if not source:
        return _check("unused_deps", "No unused dependencies", PASS, "No Python sources.")
    aliases = {"pillow": "PIL", "scikit-learn": "sklearn", "python-dotenv": "dotenv",
               "psycopg2-binary": "psycopg2", "pydantic-settings": "pydantic_settings",
               "uvicorn[standard]": "uvicorn", "pytest": "pytest"}
    # Legitimately declared without a direct import: servers, test runners, and
    # libraries pinned to control a FRAMEWORK's transitive version (pydantic under
    # fastapi, python-dotenv under pydantic-settings). Flagging these is noise, and a
    # rule that cries wolf gets ignored - which is how shared rule sets die.
    runtime_only = {"uvicorn", "gunicorn", "pytest", "pytest-asyncio", "pytest-cov",
                    "pip", "setuptools", "wheel", "websockets", "httptools",
                    "watchfiles", "uvloop", "pydantic", "python-dotenv", "python-multipart",
                    "email-validator", "anyio", "typing-extensions"}
    unused = []
    for name in declared:
        base = name.split("[")[0]
        if base in runtime_only:
            continue
        mod = aliases.get(base, base.replace("-", "_"))
        if not re.search(rf"^\s*(import|from)\s+{re.escape(mod)}\b", source, re.M):
            unused.append(base)
    if unused:
        return _check(
            "unused_deps", "No unused dependencies", WARN,
            f"Declared but never imported: {', '.join(sorted(unused)[:6])}.",
            "Remove them. Unused compiled packages are the most expensive part of a "
            "cold install and add failure modes for nothing.")
    return _check("unused_deps", "No unused dependencies", PASS,
                  f"All {len(declared)} declared packages are imported.")


def rule_env_hygiene(root: Path) -> Dict[str, Any]:
    gitignore = _read(root / ".gitignore")
    has_example = (root / ".env.example").exists()
    env_ignored = bool(re.search(r"^\s*\.env\s*$", gitignore, re.M)) or ".env" in gitignore
    if (root / ".env").exists() and not env_ignored:
        return _check("env_hygiene", "Environment file hygiene", FAIL,
                      ".env exists and is NOT gitignored.",
                      "Add .env to .gitignore immediately and rotate anything already committed.")
    if not has_example:
        return _check("env_hygiene", "Environment file hygiene", WARN,
                      "No .env.example, so required configuration is undiscoverable.",
                      "Add .env.example listing every variable with safe placeholder values.")
    return _check("env_hygiene", "Environment file hygiene", PASS,
                  ".env.example present and .env ignored.")


def rule_no_secrets(root: Path) -> Dict[str, Any]:
    hits = []
    for f in _walk(root):
        if f.suffix not in CODE_EXTS and f.name not in {".env.example", "config.json"}:
            continue
        text = _read(f, 120_000)
        for pattern, label in SECRET_PATTERNS:
            if pattern.search(text):
                hits.append(f"{f.name} ({label})")
                break
    if hits:
        return _check("no_secrets", "No committed secrets", FAIL,
                      f"Possible secrets in: {', '.join(sorted(set(hits))[:5])}.",
                      "Move them to .env, rotate the exposed values, and scrub git history.")
    return _check("no_secrets", "No committed secrets", PASS, "No secret-shaped strings found.")


def rule_tests_present(root: Path) -> Dict[str, Any]:
    tests = [p for p in _walk(root)
             if p.name.startswith("test_") and p.suffix in {".py", ".js", ".jsx", ".ts"}]
    if not tests:
        return _check("tests_present", "Automated tests exist", WARN,
                      "No test files found.",
                      "Add tests, and make every fixed bug arrive with the regression "
                      "test that would have caught it.")
    runner = any(re.search(r"--selftest|pytest|npm test|vitest", _read(root / n))
                 for n in ("run.sh", "Makefile", "package.json"))
    if not runner:
        return _check("tests_present", "Automated tests exist", WARN,
                      f"{len(tests)} test files but no documented way to run them.",
                      "Expose a single command, e.g. ./run.sh --selftest.")
    return _check("tests_present", "Automated tests exist", PASS,
                  f"{len(tests)} test files with a runner command.")


def rule_entrypoint_diagnostics(root: Path) -> Dict[str, Any]:
    """INCIDENT 2026-09-08: an app that would not start gave no way to ask why."""
    run = _read(root / "run.sh")
    if not run:
        return _check("entrypoint_flags", "Entrypoint has diagnostics", WARN,
                      "No run.sh found.", "Add run.sh as the single entrypoint.")
    missing = [f for f in ("--check", "--selftest", "--doctor") if f not in run]
    if missing:
        return _check(
            "entrypoint_flags", "Entrypoint has diagnostics", WARN,
            f"run.sh is missing: {', '.join(missing)}.",
            "--check validates invariants without installing, --selftest runs tests "
            "offline, and --doctor prints interpreter, deps, venv state and port "
            "conflicts. Without --doctor, a failure to start is guesswork.")
    return _check("entrypoint_flags", "Entrypoint has diagnostics", PASS,
                  "run.sh exposes --check, --selftest and --doctor.")


def rule_schema_migrations(root: Path) -> Dict[str, Any]:
    """INCIDENT 2026-09-07: a column was added to schema.sql, but CREATE TABLE IF NOT
    EXISTS never alters an existing database. Inserts failed at runtime."""
    files = _walk(root)
    has_schema = any(f.name.endswith(".sql") for f in files) or any(
        "CREATE TABLE" in _read(f) for f in files if f.suffix == ".py")
    if not has_schema:
        return _check("migrations", "Schema changes are migrated", PASS, "No SQL schema found.")
    has_mig = any(re.search(r"migrat|alembic|ALTER TABLE", _read(f), re.I)
                  for f in files if f.suffix in {".py", ".sql"})
    if not has_mig:
        return _check(
            "migrations", "Schema changes are migrated", WARN,
            "A SQL schema exists with no migration path. CREATE TABLE IF NOT EXISTS "
            "never alters a database that already exists, so new columns are missing "
            "at runtime rather than at startup.",
            "Add an additive migration step that ALTERs missing columns on boot.")
    return _check("migrations", "Schema changes are migrated", PASS, "Migration path present.")


def rule_data_not_committed(root: Path) -> Dict[str, Any]:
    gitignore = _read(root / ".gitignore")
    if not (root / ".git").exists():
        return _check("data_ignored", "Data is not committed", PASS, "Not a git repository.")
    risky = [p.name for p in _walk(root)
             if p.suffix in {".sqlite", ".db", ".parquet"} or p.name.endswith(".sqlite3")]
    ignored = any(tok in gitignore for tok in ("data/", "*.sqlite", "*.db"))
    if risky and not ignored:
        return _check("data_ignored", "Data is not committed", WARN,
                      f"Database files present and not gitignored: {', '.join(risky[:3])}.",
                      "Add data/ and *.sqlite to .gitignore. A committed database is "
                      "unmergeable and quietly leaks contents.")
    return _check("data_ignored", "Data is not committed", PASS,
                  "Database files are ignored or absent.")


def rule_native_deps_match_platform(root: Path) -> Dict[str, Any]:
    """INCIDENT 2026-09-08: node_modules installed on Linux, opened on macOS. rollup and
    esbuild ship per-platform native binaries as OPTIONAL deps, and npm (npm/cli#4828)
    leaves an EMPTY directory for the platform it did not install for. Every cheap check
    passed - the directory existed, .bin/vite existed - and vite died at launch."""
    import platform as _plat
    nm = root / "frontend" / "node_modules"
    if not nm.is_dir():
        nm = root / "node_modules"
    if not nm.is_dir():
        return _check("native_deps", "Native deps match this platform", PASS,
                      "No node_modules present.")
    system = {"Darwin": "darwin", "Linux": "linux", "Windows": "win32"}.get(
        _plat.system(), _plat.system().lower())
    machine = {"x86_64": "x64", "AMD64": "x64", "aarch64": "arm64",
               "arm64": "arm64"}.get(_plat.machine(), _plat.machine())
    empty = []
    for scope in ("@rollup", "@esbuild", "@swc", "@napi-rs"):
        d = nm / scope
        if not d.is_dir():
            continue
        for sub in d.iterdir():
            if not sub.is_dir():
                continue
            if system not in sub.name or machine not in sub.name:
                continue          # a platform we do not need
            has_binary = any(f.suffix == ".node" for f in sub.rglob("*.node"))
            if not has_binary:
                empty.append(f"{scope}/{sub.name}")
    if empty:
        return _check(
            "native_deps", "Native deps match this platform", FAIL,
            f"Native binary missing for this platform: {', '.join(empty[:3])}. The "
            "directory exists but holds no .node file, so every existence check passes "
            "and the app dies at launch.",
            "Test by LOADING what the bundler loads "
            "(node -e \"require('rollup');require('esbuild')\"), not by testing that a "
            "directory exists. On failure remove node_modules and reinstall; retry "
            "without package-lock.json if it persists (npm/cli#4828).")
    return _check("native_deps", "Native deps match this platform", PASS,
                  f"Native binaries present for {system}-{machine}.")


def rule_agent_brief(root: Path) -> Dict[str, Any]:
    if (root / "CLAUDE.md").exists():
        text = _read(root / "CLAUDE.md")
        n = len(re.findall(r"^\s*\d+\.", text, re.M))
        return _check("agent_brief", "Agent brief present", PASS,
                      f"CLAUDE.md present with {n} numbered invariants."
                      if n else "CLAUDE.md present.")
    return _check("agent_brief", "Agent brief present", WARN,
                  "No CLAUDE.md, so an assistant starts every session without the "
                  "project's non-negotiables.",
                  "Add CLAUDE.md with a numbered list of invariants that must never "
                  "be violated.")


RULES: List[Callable[[Path], Dict[str, Any]]] = [
    rule_interpreter_pin,
    rule_venv_not_shared,
    rule_venv_complete,
    rule_unused_dependencies,
    rule_env_hygiene,
    rule_no_secrets,
    rule_tests_present,
    rule_entrypoint_diagnostics,
    rule_schema_migrations,
    rule_data_not_committed,
    rule_native_deps_match_platform,
    rule_agent_brief,
]


def evaluate_rules(working_directory: str) -> List[Dict[str, Any]]:
    """Run every portable rule. A broken checker degrades to a WARN, never an exception."""
    root = Path(working_directory)
    if not root.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    for rule in RULES:
        try:
            out.append(rule(root))
        except Exception as exc:  # noqa: BLE001
            out.append(_check(getattr(rule, "__name__", "rule"),
                              f"Rule {getattr(rule, '__name__', '?')}", WARN,
                              f"Checker error: {type(exc).__name__}: {exc}",
                              "This is a bug in the rule, not necessarily in the project."))
    return out
