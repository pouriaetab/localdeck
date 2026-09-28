"""Can this project's Python environment actually run on this Mac?

The failure this exists to prevent, because it has already happened twice:

  A venv is a directory of symlinks pointing at an interpreter somewhere else.
  Remove or replace that interpreter and the directory stays exactly where it
  was, still looking like a perfectly good venv. Every run.sh here begins with
  some form of `[[ -d .venv ]] || python3 -m venv .venv`, so the check passes,
  the activate succeeds, and the failure surfaces much later as

      $ ...: bad CPU type in executable
      ModuleNotFoundError: No module named 'fastapi'

  macOS 27 made this everyone's problem at once: it dropped Rosetta, so every
  venv built on an Intel-only interpreter — a miniconda install, typically —
  stopped being able to start at all. Nothing on disk changed. Nothing looks
  wrong. Start simply fails.

So localdeck reads, for each project, the interpreter its venv was built on
and says so on the panel BEFORE the button is pressed. It never runs anything:
it reads `pyvenv.cfg` and, on macOS, the Mach-O header of the interpreter, which
is enough to know whether the binary can execute on this machine.

Deliberately quiet in two cases, because a warning that cries wolf is a warning
you learn to ignore:

  * No venv at all — run.sh creates one on first run. That is not a fault.
  * A dead venv sitting next to a working one (`.venv` beside
    `.venv-darwin-arm64` is exactly how a broken one gets repaired).
    The project has a usable environment; which one it picks is its business.
"""

from __future__ import annotations

import os
import struct
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Where a project keeps its virtual environment, in the order the run scripts
# here tend to look for one.
VENV_CANDIDATES = (
    ".venv",
    "venv",
    "backend/.venv",
    "backend/venv",
    "env",
)

# A platform-specific rebuild sitting next to a broken one. The suffix is what
# the repair convention in this workspace looks like, so any directory starting
# with one of the names above plus "-" is treated as a sibling environment too.
CACHE_TTL_SECONDS = 30.0

CPU_TYPE_ARM64 = 0x0100000C
CPU_TYPE_X86_64 = 0x01000007

_cache: Dict[str, tuple[float, Optional[Dict[str, Any]]]] = {}


def _read_pyvenv_home(venv: Path) -> Optional[str]:
    """The interpreter directory a venv was built from, per its own config."""
    config = venv / "pyvenv.cfg"
    try:
        text = config.read_text(errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "home":
            return value.strip()
    return None


def _read_pyvenv_version(venv: Path) -> Optional[str]:
    config = venv / "pyvenv.cfg"
    try:
        text = config.read_text(errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() in {"version", "version_info"}:
            return value.strip()
    return None


def mach_o_cpu_types(path: Path) -> Optional[List[int]]:
    """The CPU types a Mach-O binary carries, or None if it is not one.

    A fat binary lists its slices up front; a thin one names its single type in
    the header. Either way this is a handful of bytes, never an exec.
    """
    try:
        with path.open("rb") as handle:
            magic = handle.read(4)
            if len(magic) < 4:
                return None
            # Fat binaries: magic and the arch table are big-endian.
            if magic in (b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"):
                count_bytes = handle.read(4)
                if len(count_bytes) < 4:
                    return None
                count = struct.unpack(">I", count_bytes)[0]
                if count > 32:  # not a real fat header; do not trust it
                    return None
                types: List[int] = []
                for _ in range(count):
                    entry = handle.read(4)
                    if len(entry) < 4:
                        break
                    types.append(struct.unpack(">I", entry)[0])
                    # Skip the rest of the arch entry (cpusubtype, offset,
                    # size, align) — 16 more bytes for 32-bit fat headers.
                    handle.read(16)
                return types
            # Thin 64-bit Mach-O, little-endian (every modern macOS binary).
            if magic == b"\xcf\xfa\xed\xfe":
                cpu = handle.read(4)
                if len(cpu) < 4:
                    return None
                return [struct.unpack("<I", cpu)[0]]
            # Thin 64-bit Mach-O, big-endian (historical).
            if magic == b"\xfe\xed\xfa\xcf":
                cpu = handle.read(4)
                if len(cpu) < 4:
                    return None
                return [struct.unpack(">I", cpu)[0]]
    except OSError:
        return None
    return None


def _inspect_venv(root: Path, relative: str, *, platform: str, machine: str) -> Dict[str, Any]:
    """Whether one venv can run, and if not, in plain words why not."""
    venv = root / relative
    python = venv / "bin" / "python3"
    if not python.exists() and (venv / "bin" / "python").exists():
        python = venv / "bin" / "python"

    home = _read_pyvenv_home(venv)
    version = _read_pyvenv_version(venv)
    result: Dict[str, Any] = {
        "path": relative,
        "home": home,
        "version": version,
        "usable": True,
        "reason": None,
    }

    # The interpreter the venv links to is simply gone.
    if not python.exists():
        result["usable"] = False
        result["reason"] = (
            f"its interpreter is missing — {relative}/bin/python3 does not resolve to a file"
        )
        return result

    if home and not Path(home).is_dir():
        result["usable"] = False
        result["reason"] = f"it was built on {home}, which is not on this machine any more"
        return result

    # macOS 27 removed Rosetta: an Intel-only interpreter cannot start at all,
    # and the error it gives ("bad CPU type in executable") names neither the
    # venv nor the project.
    if platform == "darwin" and machine == "arm64":
        types = mach_o_cpu_types(python.resolve() if python.is_symlink() else python)
        if types is not None and CPU_TYPE_ARM64 not in types:
            kind = "Intel-only" if CPU_TYPE_X86_64 in types else "the wrong architecture"
            result["usable"] = False
            result["reason"] = (
                f"its interpreter is {kind}, and this Mac has no Rosetta — "
                f"starting it fails with 'bad CPU type in executable'"
            )
    return result


def _sibling_candidates(root: Path) -> List[str]:
    """Repaired environments living beside a base name (`.venv-darwin-arm64`)."""
    found: List[str] = []
    for parent, base in ((root, ""), (root / "backend", "backend/")):
        if not parent.is_dir():
            continue
        try:
            entries = sorted(entry.name for entry in parent.iterdir() if entry.is_dir())
        except OSError:
            continue
        for name in entries:
            for stem in (".venv", "venv", "env"):
                if name.startswith(stem + "-"):
                    found.append(base + name)
    return found


def _inspect(working_directory: str, *, platform: str, machine: str) -> Optional[Dict[str, Any]]:
    try:
        root = Path(working_directory).expanduser()
        if not root.is_dir():
            return None
    except OSError:
        return None

    relatives = [r for r in VENV_CANDIDATES if (root / r / "pyvenv.cfg").is_file()]
    relatives += [
        r for r in _sibling_candidates(root)
        if (root / r / "pyvenv.cfg").is_file() and r not in relatives
    ]
    if not relatives:
        # No environment yet. run.sh builds one on first run; nothing is wrong.
        return None

    venvs = [_inspect_venv(root, r, platform=platform, machine=machine) for r in relatives]
    usable = [v for v in venvs if v["usable"]]
    return {
        "venvs": venvs,
        # The project has SOMETHING it can run with. Which one its run.sh picks
        # is the project's business, and guessing at that is how a check starts
        # crying wolf.
        "has_usable": bool(usable),
        "broken": [v for v in venvs if not v["usable"]],
    }


def status(
    working_directory: str,
    *,
    refresh: bool = False,
    platform: Optional[str] = None,
    machine: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Cached environment status for a project, or None when there is no venv."""
    platform = platform if platform is not None else sys.platform
    machine = machine if machine is not None else os.uname().machine
    now = time.monotonic()
    key = f"{working_directory}|{platform}|{machine}"
    cached = _cache.get(key)
    if cached and not refresh and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]
    try:
        result = _inspect(working_directory, platform=platform, machine=machine)
    except Exception:
        # A dashboard must never fail to render because a check failed.
        result = None
    _cache[key] = (now, result)
    return result


def rebuild_hint(root: Path, relative: str) -> str:
    """The exact command that repairs this venv, requirements file included."""
    parent = str(Path(relative).parent)
    prefix = "" if parent == "." else f"cd {parent} && "
    name = Path(relative).name
    for requirements in ("requirements.txt", "requirements-dev.txt"):
        if (root / (("" if parent == "." else parent + "/") + requirements)).is_file():
            return (
                f"{prefix}rm -rf {name} && python3 -m venv {name} && "
                f"{name}/bin/pip install -r {requirements}"
            )
    return f"{prefix}rm -rf {name} && python3 -m venv {name}"


def warnings_for(env: Optional[Dict[str, Any]], working_directory: str = "") -> List[str]:
    """Lines to show when a project has no Python environment that can run."""
    if not env or env["has_usable"] or not env["broken"]:
        return []
    root = Path(working_directory).expanduser() if working_directory else Path(".")
    lines: List[str] = []
    for venv in env["broken"]:
        lines.append(
            f"{venv['path']} cannot run: {venv['reason']}. "
            f"Start will fail until it is rebuilt — {rebuild_hint(root, venv['path'])}"
        )
    return lines
