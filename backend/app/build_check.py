"""Is the app a project serves actually built from its current source?

The failure this exists to prevent, in full, because it cost a day to find and
looked like nothing at all:

  A project serves a built single-page app -- FastAPI hands out `index.html`,
  which names content-hashed bundles (`/assets/index-ABC123.js`). Rebuild the
  UI and the hash changes, so `index.html` changes too. If that `index.html`
  is served without `Cache-Control: no-store`, a browser is free to keep its
  copy: no explicit freshness plus a `Last-Modified` means the browser invents
  a lifetime and reuses the file without asking. The tab then keeps requesting
  YESTERDAY's bundle -- and because a build that does not wipe its output
  directory leaves old bundles lying next to the new one, that request
  SUCCEEDS. 200 OK. No error anywhere. The app simply is the old version,
  through restarts of the app, of localdeck, and of the browser, because an
  HTTP disk cache outlives all three. Worse, the cache is keyed by origin, so
  the same app on a different port looks perfectly up to date -- which is what
  makes people conclude the launcher is at fault.

So this module answers three questions for every project, and localdeck
reports the answers on the panel instead of leaving them to be discovered:

  1. Is the built output older than the source it was built from?
  2. Are there orphaned bundles next to it -- the thing that turns a stale
     reference from a loud 404 into a silently wrong app?
  3. (in runtime.py, from the preview probe) does the app let its HTML be
     cached at all?

`fingerprint` is the other half: localdeck appends it to the URL it opens,
so a rebuilt app is a new URL and no cached HTML can be reused for it.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

# Where a built SPA's entry point tends to land, most specific first.
OUTPUT_CANDIDATES = (
    "static/index.html",
    "frontend/dist/index.html",
    "web/dist/index.html",
    "ui/dist/index.html",
    "dist/index.html",
    "build/index.html",
    "frontend/build/index.html",
)

# The sources a build of that entry point is made from.
SOURCE_CANDIDATES = (
    "frontend/src",
    "web/src",
    "ui/src",
    "src",
    "frontend/index.html",
    "web/index.html",
    "ui/index.html",
    "frontend/vite.config.js",
    "frontend/vite.config.ts",
    "web/vite.config.js",
    "web/vite.config.ts",
    "vite.config.js",
    "vite.config.ts",
)

SKIP_DIRS = {
    "node_modules", "__pycache__", ".git", ".venv", "venv", "dist", "build",
    "static", ".next", ".cache", "coverage", ".pytest_cache",
}

# A source tree we cannot read cheaply is not worth a slow dashboard.
MAX_FILES_SCANNED = 4000

# How long a result is reused. serialize_state() runs on every broadcast, so
# this must never be a filesystem walk per call.
CACHE_TTL_SECONDS = 30.0

_cache: Dict[str, tuple[float, Optional[Dict[str, Any]]]] = {}


def _newest_source(root: Path) -> tuple[float, Optional[Path]]:
    """(mtime, path) of the most recently modified source file under `root`."""
    best_time = 0.0
    best_path: Optional[Path] = None
    scanned = 0
    for relative in SOURCE_CANDIDATES:
        candidate = root / relative
        if not candidate.exists():
            continue
        if candidate.is_file():
            mtime = candidate.stat().st_mtime
            if mtime > best_time:
                best_time, best_path = mtime, candidate
            continue
        for directory, subdirs, files in os.walk(candidate):
            subdirs[:] = [d for d in subdirs if d not in SKIP_DIRS and not d.startswith(".")]
            for name in files:
                if name.startswith("."):
                    continue
                scanned += 1
                if scanned > MAX_FILES_SCANNED:
                    return best_time, best_path
                path = Path(directory) / name
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                if mtime > best_time:
                    best_time, best_path = mtime, path
    return best_time, best_path


def _orphan_bundles(index_html: Path) -> int:
    """Hashed bundles sitting next to index.html that it does not reference.

    Each one is a stale URL that still answers 200, so a browser holding an old
    index.html keeps running old code instead of failing visibly.
    """
    assets = index_html.parent / "assets"
    if not assets.is_dir():
        return 0
    try:
        markup = index_html.read_text(errors="replace")
    except OSError:
        return 0
    referenced = set(re.findall(r"[\w./-]*assets/([A-Za-z0-9._-]+)", markup))
    orphans = 0
    for entry in assets.iterdir():
        if not entry.is_file():
            continue
        # Only content-hashed build output, never hand-placed files (logo.png).
        if re.match(r"^[A-Za-z0-9_.-]+-[A-Za-z0-9_-]{6,}\.(js|css)$", entry.name):
            if entry.name not in referenced:
                orphans += 1
    return orphans


def _inspect(working_directory: str) -> Optional[Dict[str, Any]]:
    try:
        root = Path(working_directory).expanduser()
        if not root.is_dir():
            return None
    except OSError:
        return None

    index_html = next((root / c for c in OUTPUT_CANDIDATES if (root / c).is_file()), None)
    if index_html is None:
        # Nothing built is served from disk: a dev server compiles every request,
        # or this is not a web project. Either way there is nothing to go stale.
        return None

    built_at = index_html.stat().st_mtime
    source_at, source_path = _newest_source(root)

    # One second of slack: a build writes its output within the same second as
    # the last source it read, and a filesystem's timestamp granularity varies.
    stale = bool(source_at and source_at > built_at + 1.0)

    return {
        "index": str(index_html.relative_to(root)),
        # Changes on every rebuild. localdeck puts it in the URL it opens so
        # a rebuilt app cannot be served from a browser's cache.
        "fingerprint": str(int(built_at)),
        "built_at": dt.datetime.fromtimestamp(built_at).isoformat(timespec="seconds"),
        "stale": stale,
        "newer_source": (
            str(source_path.relative_to(root)) if stale and source_path else None
        ),
        "orphan_bundles": _orphan_bundles(index_html),
    }


def status(working_directory: str, *, refresh: bool = False) -> Optional[Dict[str, Any]]:
    """Cached build status for a project, or None when nothing built is served."""
    now = time.monotonic()
    cached = _cache.get(working_directory)
    if cached and not refresh and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]
    try:
        result = _inspect(working_directory)
    except Exception:
        # A dashboard must never fail to render because a check failed.
        result = None
    _cache[working_directory] = (now, result)
    return result


def warnings_for(build: Optional[Dict[str, Any]]) -> list[str]:
    """Lines to show in the project's terminal when something looks stale."""
    if not build:
        return []
    lines: list[str] = []
    if build["stale"]:
        lines.append(
            f"localdeck: {build['index']} is OLDER than {build['newer_source']} "
            f"(built {build['built_at']}). This project is serving a stale build — "
            f"rebuild its UI, or its run.sh should."
        )
    if build["orphan_bundles"]:
        lines.append(
            f"localdeck: {build['orphan_bundles']} unreferenced bundle(s) next to "
            f"{build['index']}. Old bundles left on disk let a cached page load old "
            f"code with no error — a build that empties its output directory prevents it."
        )
    return lines


def classify_html(head: bytes) -> Optional[str]:
    """"dev" (compiled per request), "built" (a bundle from disk), or None."""
    try:
        text = head.decode("utf-8", errors="replace")
    except Exception:  # pragma: no cover - decode with errors never raises
        return None
    if "/@vite/client" in text or "__vite_ping" in text or "/@react-refresh" in text:
        return "dev"
    if re.search(r"""["']/?[\w./-]*assets/[\w.-]+-[\w-]{6,}\.(?:js|css)""", text):
        return "built"
    if "<html" in text.lower():
        return "other"
    return None

def html_is_cacheable(cache_control: Optional[str]) -> Optional[bool]:
    """Is a browser free to reuse this HTML without asking the server?

    No Cache-Control at all is the dangerous case, not a safe default: with a
    Last-Modified and no explicit lifetime a browser invents one, so the page
    can be reused for hours after a rebuild.
    """
    if cache_control is None:
        return True
    value = cache_control.lower()
    return not any(
        token in value for token in ("no-store", "no-cache", "max-age=0")
    )
