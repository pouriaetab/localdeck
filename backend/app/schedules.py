"""macOS launchd scheduler integration for localdeck.

Lists the user's LaunchAgents (~/Library/LaunchAgents), summarizes each one's
schedule and status, and exposes pause / resume / run-now / stop / reschedule
actions via `launchctl`. Read/parse and schedule generation are pure functions
(unit-tested); the launchctl actions are thin wrappers around standard commands.
"""
from __future__ import annotations

import datetime as dt
import os
import plistlib
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"

# launchd weekday numbers: 0 or 7 = Sunday, 1 = Monday ... 6 = Saturday.
WEEKDAY_NAMES = {0: "Sun", 1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}
_INTERPRETERS = {"/bin/bash", "/bin/zsh", "/bin/sh", "bash", "zsh", "sh", "python", "python3", "/usr/bin/env"}


# --------------------------------------------------------------------------- #
# launchctl helpers
# --------------------------------------------------------------------------- #
def _run(args: List[str], timeout: int = 10) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except Exception as exc:  # noqa: BLE001
        return 1, "", str(exc)


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def launchctl_status() -> Dict[str, Dict[str, Any]]:
    """label -> {pid, last_exit} from `launchctl list` (3 cols: PID, LastExit, Label)."""
    rc, out, _ = _run(["launchctl", "list"])
    status: Dict[str, Dict[str, Any]] = {}
    if rc != 0:
        return status
    for line in out.splitlines()[1:]:
        cols = line.split("\t")
        if len(cols) >= 3 and cols[2].strip():
            status[cols[2].strip()] = {
                "pid": _safe_int(cols[0].strip()),
                "last_exit": _safe_int(cols[1].strip()),
            }
    return status


def loaded_labels() -> set:
    """Labels currently loaded into launchd."""
    return set(launchctl_status().keys())


# --------------------------------------------------------------------------- #
# Parsing / summarizing (pure)
# --------------------------------------------------------------------------- #
def _fmt_time(hour: Any, minute: Any) -> str:
    return f"{int(hour or 0):02d}:{int(minute or 0):02d}"


def _norm_weekday(value: int) -> int:
    value = int(value)
    return 0 if value == 7 else value


def summarize_schedule(plist: Dict[str, Any]) -> str:
    if "StartInterval" in plist:
        secs = int(plist["StartInterval"])
        if secs % 3600 == 0:
            return f"every {secs // 3600}h"
        if secs % 60 == 0:
            return f"every {secs // 60}m"
        return f"every {secs}s"

    sci = plist.get("StartCalendarInterval")
    if sci is None:
        return "at load / manual"

    entries = sci if isinstance(sci, list) else [sci]
    times: set = set()
    days: set = set()
    doms: set = set()
    for entry in entries:
        times.add(_fmt_time(entry.get("Hour", 0), entry.get("Minute", 0)))
        if "Weekday" in entry:
            days.add(_norm_weekday(entry["Weekday"]))
        if "Day" in entry:
            doms.add(int(entry["Day"]))
    time_str = ", ".join(sorted(times))

    if doms:
        return f"Day {', '.join(str(d) for d in sorted(doms))} at {time_str}"
    if days:
        ordered = sorted(days)
        if ordered == [1, 2, 3, 4, 5]:
            day_str = "Weekdays"
        elif ordered == [0, 6]:
            day_str = "Weekends"
        else:
            day_str = ", ".join(WEEKDAY_NAMES[d] for d in ordered)
        return f"{day_str} at {time_str}"
    return f"Daily at {time_str}"


def raw_schedule(plist: Dict[str, Any]) -> Dict[str, Any]:
    """Structured schedule for prefilling the reschedule form."""
    sci = plist.get("StartCalendarInterval")
    if not sci:
        return {"hour": 9, "minute": 0, "weekdays": [1, 2, 3, 4, 5]}
    entries = sci if isinstance(sci, list) else [sci]
    first = entries[0]
    weekdays = sorted({_norm_weekday(e["Weekday"]) for e in entries if "Weekday" in e})
    return {
        "hour": int(first.get("Hour", 9)),
        "minute": int(first.get("Minute", 0)),
        "weekdays": weekdays or [1, 2, 3, 4, 5],
    }


def derive_description(args: List[str]) -> str:
    parts: List[str] = []
    skip = False
    for arg in args:
        if skip:
            skip = False
            continue
        if arg.startswith("--"):
            skip = True  # also skip its value
            continue
        if arg in _INTERPRETERS or arg.endswith("/python") or arg.endswith("/python3") or arg == "-m":
            continue
        if arg.startswith("/"):
            base = os.path.basename(arg)
            if base.endswith(".json") or "config" in base:
                continue
            parts.append(base)
        else:
            parts.append(arg)
    return " ".join(parts[:4]) or "scheduled task"


def _project_name(working_dir: Optional[str], args: List[str]) -> str:
    if working_dir:
        return os.path.basename(str(working_dir).rstrip("/"))
    # No working directory: name it after the folder holding the script it runs.
    for arg in args:
        if isinstance(arg, str) and arg.startswith("/") and "." in os.path.basename(arg):
            return os.path.basename(os.path.dirname(arg)) or "—"
    return "—"


def _last_run(plist: Dict[str, Any]) -> Optional[str]:
    """Approximate the last run time from the job's log file timestamps."""
    times: List[float] = []
    for key in ("StandardOutPath", "StandardErrorPath"):
        p = plist.get(key)
        if p and os.path.exists(p):
            try:
                times.append(os.path.getmtime(p))
            except OSError:
                pass
    if not times:
        return None
    return dt.datetime.fromtimestamp(max(times)).strftime("%Y-%m-%d %H:%M")


def parse_plist(path: Path, status: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    try:
        data = plistlib.loads(path.read_bytes())
    except Exception:  # noqa: BLE001
        return None
    label = data.get("Label", path.stem)
    args = data.get("ProgramArguments", []) or []
    disabled = bool(data.get("Disabled", False))
    info = status.get(label, {})
    is_loaded = label in status
    pid = info.get("pid")
    last_exit = info.get("last_exit")

    if pid:
        run_status = "running"
    elif last_exit == 0:
        run_status = "success"
    elif last_exit is not None:
        run_status = f"failed ({last_exit})"
    else:
        run_status = "—"

    return {
        "label": label,
        "project": _project_name(data.get("WorkingDirectory"), args),
        "description": data.get("Comment") or data.get("Description") or derive_description(args),
        "schedule": summarize_schedule(data),
        "raw_schedule": raw_schedule(data),
        "status": "active" if (is_loaded and not disabled) else "paused",
        "loaded": is_loaded,
        "disabled": disabled,
        "last_run": _last_run(data) or "—",
        "run_status": run_status,
        "last_exit": last_exit,
        "plist": str(path),
    }


def list_tasks(agents_dir: Optional[str] = None, status: Optional[Dict[str, Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    agents = Path(agents_dir) if agents_dir else LAUNCH_AGENTS
    if status is None:
        status = launchctl_status()
    if not agents.is_dir():
        return []
    tasks = []
    for path in sorted(agents.glob("*.plist")):
        parsed = parse_plist(path, status)
        if parsed:
            tasks.append(parsed)
    return tasks


def tail_log(label: str, lines: int = 40, agents_dir: Optional[str] = None) -> Dict[str, Any]:
    """Return the last N lines of the job's stdout and stderr logs."""
    plist = _find_plist(label, agents_dir)
    if plist is None:
        raise ValueError(f"No LaunchAgent found for {label}.")
    data = plistlib.loads(plist.read_bytes())

    def _tail(path: Optional[str]) -> str:
        if not path or not os.path.exists(path):
            return ""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return "".join(f.readlines()[-lines:])
        except OSError:
            return ""

    return {
        "label": label,
        "stdout": _tail(data.get("StandardOutPath")),
        "stderr": _tail(data.get("StandardErrorPath")),
        "stdout_path": data.get("StandardOutPath"),
        "stderr_path": data.get("StandardErrorPath"),
    }


def build_calendar_interval(hour: int, minute: int, weekdays: List[int]) -> List[Dict[str, int]]:
    hour = max(0, min(23, int(hour)))
    minute = max(0, min(59, int(minute)))
    days = [int(d) for d in weekdays] or [1, 2, 3, 4, 5]
    return [{"Weekday": d, "Hour": hour, "Minute": minute} for d in days]


# --------------------------------------------------------------------------- #
# Actions (launchctl)
# --------------------------------------------------------------------------- #
def _find_plist(label: str, agents_dir: Optional[str] = None) -> Optional[Path]:
    agents = Path(agents_dir) if agents_dir else LAUNCH_AGENTS
    if not agents.is_dir():
        return None
    # A launchd label is an identifier, never a path. Refuse anything that could
    # walk out of the LaunchAgents directory (e.g. "../../tmp/evil" or an
    # absolute path) - otherwise a crafted {label} would let a request read,
    # rewrite (reschedule) or `launchctl load` a .plist anywhere on disk.
    if not label or "/" in label or "\\" in label or "\x00" in label:
        return None
    candidate = agents / f"{label}.plist"
    try:
        candidate.resolve().relative_to(agents.resolve())
    except (ValueError, OSError, RuntimeError):
        return None
    if candidate.exists():
        return candidate
    for path in agents.glob("*.plist"):
        try:
            if plistlib.loads(path.read_bytes()).get("Label") == label:
                return path
        except Exception:  # noqa: BLE001
            continue
    return None


def perform_action(label: str, action: str) -> Dict[str, Any]:
    plist = _find_plist(label)
    if plist is None:
        raise ValueError(f"No LaunchAgent found for {label}.")
    plist_str = str(plist)

    if action == "pause":
        rc, out, err = _run(["launchctl", "unload", "-w", plist_str])
    elif action == "resume":
        rc, out, err = _run(["launchctl", "load", "-w", plist_str])
    elif action == "run":
        rc, out, err = _run(["launchctl", "start", label])
    elif action == "stop":
        rc, out, err = _run(["launchctl", "stop", label])
    else:
        raise ValueError(f"Unknown action: {action}")

    return {"ok": rc == 0, "action": action, "label": label, "output": (out or err).strip()}


def reschedule(label: str, hour: int, minute: int, weekdays: List[int]) -> Dict[str, Any]:
    plist = _find_plist(label)
    if plist is None:
        raise ValueError(f"No LaunchAgent found for {label}.")
    data = plistlib.loads(plist.read_bytes())
    data.pop("StartInterval", None)
    data["StartCalendarInterval"] = build_calendar_interval(hour, minute, weekdays)
    plist.write_bytes(plistlib.dumps(data))
    # Reload so the new schedule takes effect.
    _run(["launchctl", "unload", "-w", str(plist)])
    rc, out, err = _run(["launchctl", "load", "-w", str(plist)])
    return {"ok": rc == 0, "label": label, "schedule": summarize_schedule(data), "output": (out or err).strip()}
