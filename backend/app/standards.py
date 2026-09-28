"""Project conformance checks — the "house standard" enforced by localdeck.

The checker is read-only: it inspects a project's folder and produces a
scorecard. It never modifies the project. Remediation is handled separately by
writing an approved fix-plan document into the project (see runtime.apply_conformance).
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, List

SKIP_DIRS = {
    "node_modules",
    ".git",
    "venv",
    ".venv",
    "env",
    "__pycache__",
    "dist",
    "build",
    ".next",
    ".tools",
    ".pnpm-store",
    ".pycache",
    "catboost_info",
    ".cache",
    "data_cache",
}

CODE_EXTS = {".py", ".js", ".jsx", ".ts", ".tsx", ".sh"}
MAX_FILE_BYTES = 400_000

# Status values, worst-first for aggregation.
FAIL = "fail"
WARN = "warn"
PASS = "pass"
_ORDER = {PASS: 0, WARN: 1, FAIL: 2}


def _walk(root: Path, max_depth: int = 3) -> List[Path]:
    files: List[Path] = []

    def rec(directory: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            entries = list(directory.iterdir())
        except OSError:
            return
        for entry in entries:
            try:
                if entry.is_dir():
                    if entry.name in SKIP_DIRS or entry.name.startswith("."):
                        continue
                    rec(entry, depth + 1)
                elif entry.is_file():
                    files.append(entry)
            except OSError:
                continue

    if root.is_dir():
        rec(root, 0)
    return files


def _read_text(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _load_package_jsons(files: List[Path]) -> List[Dict[str, Any]]:
    packages: List[Dict[str, Any]] = []
    for path in files:
        if path.name == "package.json":
            try:
                packages.append(json.loads(_read_text(path) or "{}"))
            except json.JSONDecodeError:
                continue
    return packages


def _all_deps(pkg: Dict[str, Any]) -> Dict[str, str]:
    deps: Dict[str, str] = {}
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        value = pkg.get(key)
        if isinstance(value, dict):
            deps.update(value)
    return deps


def _check(check_id: str, title: str, status: str, detail: str, suggestion: str = "") -> Dict[str, Any]:
    return {
        "id": check_id,
        "title": title,
        "status": status,
        "detail": detail,
        "suggestion": suggestion,
    }


from . import standards_rules  # portable, incident-derived rules


def evaluate(working_directory: str) -> Dict[str, Any]:
    root = Path(working_directory)
    checks: List[Dict[str, Any]] = []

    if not root.is_dir():
        checks.append(
            _check(
                "exists",
                "Working directory",
                FAIL,
                f"Directory not found: {working_directory}",
                "Fix the working directory path in the project settings.",
            )
        )
        return _finish(checks)

    files = _walk(root)
    py_files = [f for f in files if f.suffix == ".py"]
    web_files = [f for f in files if f.suffix in {".js", ".jsx", ".ts", ".tsx"}]
    packages = _load_package_jsons(files)

    # ---- 1. React + Vite frontend -----------------------------------------
    has_react = any("react" in _all_deps(pkg) for pkg in packages)
    has_vite = any("vite" in _all_deps(pkg) for pkg in packages)
    if has_react and has_vite:
        checks.append(_check("react_vite", "React + Vite frontend", PASS, "React and Vite found in package.json."))
    elif has_react:
        checks.append(
            _check(
                "react_vite",
                "React + Vite frontend",
                WARN,
                "React found, but Vite was not detected.",
                "Migrate the frontend build tooling to Vite (add `vite` and `@vitejs/plugin-react`, "
                "add a `vite.config.js`, and set `dev`/`build` scripts to use Vite).",
            )
        )
    else:
        checks.append(
            _check(
                "react_vite",
                "React + Vite frontend",
                FAIL,
                "No React + Vite frontend detected.",
                "Add a React + Vite frontend (e.g. a `frontend/` app created with `npm create vite@latest -- --template react`).",
            )
        )

    # ---- 2. FastAPI backend + WebSockets ----------------------------------
    fastapi_used = any("fastapi" in _read_text(f).lower() for f in py_files)
    ws_backend = any(
        ("websocket" in (text := _read_text(f).lower())) and ("fastapi" in text or "@app.websocket" in text or "websocket" in text)
        for f in py_files
    )
    ws_frontend = any("new websocket" in _read_text(f).lower() for f in web_files)
    if fastapi_used and (ws_backend or ws_frontend):
        checks.append(
            _check(
                "fastapi_ws",
                "FastAPI backend + WebSockets",
                PASS,
                "FastAPI backend and WebSocket usage detected.",
            )
        )
    elif fastapi_used:
        checks.append(
            _check(
                "fastapi_ws",
                "FastAPI backend + WebSockets",
                WARN,
                "FastAPI found, but no WebSocket channel detected.",
                "Add a FastAPI WebSocket endpoint (`@app.websocket('/ws')`) and connect to it from the "
                "React frontend with `new WebSocket(...)` so data is piped live to the UI.",
            )
        )
    else:
        checks.append(
            _check(
                "fastapi_ws",
                "FastAPI backend + WebSockets",
                FAIL,
                "No FastAPI backend detected.",
                "Add a FastAPI backend that parses the data and streams it to the React frontend over WebSockets.",
            )
        )

    # ---- 3. Clean stop / localhost-only -----------------------------------
    exposed_files = []
    for f in (f for f in files if f.suffix in CODE_EXTS):
        text = _read_text(f)
        if "0.0.0.0" in text:
            exposed_files.append(f.name)
    if exposed_files:
        checks.append(
            _check(
                "localhost",
                "Clean stop / localhost-only",
                WARN,
                f"Found 0.0.0.0 binding in: {', '.join(sorted(set(exposed_files))[:5])}.",
                "Bind servers to 127.0.0.1 by default (only expose 0.0.0.0 when explicitly intended). "
                "localdeck already force-stops the process group and frees the port on Stop.",
            )
        )
    else:
        checks.append(
            _check(
                "localhost",
                "Clean stop / localhost-only",
                PASS,
                "No 0.0.0.0 bindings found; localdeck force-stops and frees the port on Stop.",
            )
        )

    # ---- 4. Standard project layout ---------------------------------------
    names = {p.name for p in root.iterdir()} if root.is_dir() else set()
    has_frontend = "frontend" in names or has_react
    has_backend = "backend" in names or fastapi_used
    has_readme = any(n.lower().startswith("readme") for n in names)
    has_dev_script = (
        (root / "scripts" / "dev.sh").exists()
        or (root / "scripts" / "dev.py").exists()
        or (root / "Makefile").exists()
        or any("dev" in (pkg.get("scripts") or {}) for pkg in packages)
    )
    layout_score = sum([has_frontend, has_backend, has_readme, has_dev_script])
    missing = [
        label
        for present, label in [
            (has_frontend, "frontend app"),
            (has_backend, "backend app"),
            (has_dev_script, "dev script (scripts/dev.sh, Makefile, or npm `dev`)"),
            (has_readme, "README"),
        ]
        if not present
    ]
    if layout_score == 4:
        checks.append(_check("layout", "Standard project layout", PASS, "frontend, backend, dev script, and README all present."))
    elif layout_score >= 2:
        checks.append(
            _check(
                "layout",
                "Standard project layout",
                WARN,
                f"Missing: {', '.join(missing)}.",
                "Adopt the standard layout: a `frontend/` app, a `backend/` app, a one-command dev script, and a README.",
            )
        )
    else:
        checks.append(
            _check(
                "layout",
                "Standard project layout",
                FAIL,
                f"Missing: {', '.join(missing)}.",
                "Restructure into the standard layout: `frontend/`, `backend/`, a `scripts/dev.sh` (or Makefile) that starts both, and a README.",
            )
        )

    checks.extend(standards_rules.evaluate_rules(working_directory))
    return _finish(checks)


def _finish(checks: List[Dict[str, Any]]) -> Dict[str, Any]:
    overall = PASS
    for check in checks:
        if _ORDER[check["status"]] > _ORDER[overall]:
            overall = check["status"]
    counts = {PASS: 0, WARN: 0, FAIL: 0}
    for check in checks:
        counts[check["status"]] += 1
    return {
        "overall": overall,
        "counts": counts,
        "checks": checks,
        "checked_at": dt.datetime.utcnow().isoformat() + "Z",
    }


def generate_plan(project_name: str, working_directory: str, scorecard: Dict[str, Any]) -> str:
    """Build a human/agent-readable fix-request document from a scorecard."""
    when = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines: List[str] = []
    lines.append("# localdeck: Standards Fix Request")
    lines.append("")
    lines.append(f"_Generated for **{project_name}** on {when}._")
    lines.append("")
    lines.append(
        "localdeck maintains a shared standard across projects: a **React + Vite** "
        "frontend, a **FastAPI** backend that streams data to the frontend over "
        "**WebSockets**, **localhost-only** binding with a clean stop, and a "
        "**standard project layout**. The items below were flagged as not meeting that "
        "standard. Please apply the suggested changes."
    )
    lines.append("")
    lines.append(f"Overall status: **{scorecard['overall'].upper()}**")
    lines.append("")

    actionable = [c for c in scorecard["checks"] if c["status"] in (WARN, FAIL)]
    if not actionable:
        lines.append("✅ This project already meets all current standards. No action needed.")
        return "\n".join(lines) + "\n"

    for check in actionable:
        badge = "❌ FAIL" if check["status"] == FAIL else "⚠️ WARN"
        lines.append(f"## {badge} — {check['title']}")
        lines.append("")
        lines.append(f"**Finding:** {check['detail']}")
        lines.append("")
        if check.get("suggestion"):
            lines.append(f"**Required change:** {check['suggestion']}")
            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("After applying these changes, re-run the standards check in localdeck to confirm the project passes.")
    return "\n".join(lines) + "\n"
