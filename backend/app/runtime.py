from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse, urlunparse

from fastapi import WebSocket

from . import build_check, env_check, standards
from .models import AppState, PanelLayout, ProjectConfig, ProjectType
from .process_manager import ProcessManager, ProcessManagerError
from .storage import StateStore

# Cadence of the websocket heartbeat. The frontend calls a connection dead after
# roughly three missed beats, so this also sets how fast a silently dead
# connection is noticed and replaced.
HEARTBEAT_SECONDS = 15


class AppRuntime:
    def __init__(self, state_file: Path) -> None:
        self.store = StateStore(state_file)
        self.state: AppState = self.store.load()
        self.state.projects.sort(key=lambda project: (project.order, project.name.lower()))
        self.manager = ProcessManager(self._emit_event)
        self.manager.attach_projects(self.state.projects)
        self.connections: set[WebSocket] = set()
        self.preview_state: Dict[str, Dict[str, Any]] = {}
        self.preview_task: Optional[asyncio.Task[None]] = None
        self.heartbeat_task: Optional[asyncio.Task[None]] = None
        # project id -> the build problems last reported for it, so a 2.5s probe
        # loop neither repeats itself nor leaves a fixed problem on screen.
        self._reported_build_problems: Dict[str, list[str]] = {}
        for project in self.state.projects:
            self.preview_state[project.id] = self._default_preview(project)

    async def startup(self) -> None:
        # Always open on the main dashboard, not a maximized/focused panel from
        # a previous session.
        if self.state.dashboard.expanded_project_id is not None:
            self.state.dashboard.expanded_project_id = None
            self._persist()
        self.preview_task = asyncio.create_task(self._preview_loop())
        self.heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        for project in self.state.projects:
            if project.auto_start:
                # Say so on BOTH paths. This used to swallow every failure, so a
                # project marked auto-start that could not come up looked exactly
                # like one that was never marked -- silently stopped, with nothing
                # anywhere to read. (Once an app was stopped by a dashboard
                # restart and stayed down 21 minutes because nothing restarted
                # it and nothing said a word.)
                try:
                    await self.manager.start(project)
                    print(f"[deck] auto-start: {project.name} started", flush=True)
                except ProcessManagerError as exc:
                    print(f"[deck] auto-start FAILED for {project.name}: {exc}",
                          flush=True)
                except Exception as exc:          # never let one project stop the rest
                    print(f"[deck] auto-start FAILED for {project.name}: "
                          f"{type(exc).__name__}: {exc}", flush=True)

    async def shutdown(self) -> None:
        for task in (self.preview_task, self.heartbeat_task):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        await self.manager.shutdown()

    def serialize_state(self) -> Dict[str, Any]:
        return {
            "dashboard": self.state.dashboard.to_dict(),
            "projects": [self.serialize_project(project) for project in self.state.projects],
        }

    def serialize_project(self, project: ProjectConfig) -> Dict[str, Any]:
        payload = project.to_dict()
        payload["runtime"] = self.manager.snapshot(project.id)
        payload["preview"] = self.preview_state.get(project.id, self._default_preview(project))
        # Cheap (cached) answer to "is what this project serves built from its
        # current source?" — see build_check for why that question needs asking.
        payload["build"] = build_check.status(project.working_directory)
        # Cheap (cached) answer to "can this project's Python actually run on
        # this machine?" — a venv directory is not a working venv, and macOS 27
        # dropping Rosetta turned that difference into a silent Start failure.
        payload["env"] = env_check.status(project.working_directory)
        return payload

    def get_project(self, project_id: str) -> ProjectConfig:
        for project in self.state.projects:
            if project.id == project_id:
                return project
        raise KeyError(project_id)

    async def add_project(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        project = ProjectConfig.from_dict(
            {
                **payload,
                "id": self._generate_project_id(payload.get("id") or payload["name"]),
            }
        )
        # New projects go to the end of the sidebar order.
        project.order = max((p.order for p in self.state.projects), default=-1) + 1
        self.state.projects.append(project)
        self._ensure_category(project.category)
        self._sort_projects()
        self.preview_state[project.id] = self._default_preview(project)
        self.manager.attach_projects(self.state.projects)
        self._persist()
        await self.broadcast_snapshot()
        return self.serialize_project(project)

    async def update_project(self, project_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        project = self.get_project(project_id)
        was_active = self.manager.snapshot(project_id)["status"] in {"Starting", "Running", "Stopping"}

        updated = ProjectConfig.from_dict(
            {
                **payload,
                "id": project_id,
            }
        )

        index = self.state.projects.index(project)
        self.state.projects[index] = updated
        self._ensure_category(updated.category)
        self.preview_state[project_id] = self._default_preview(updated)
        self.manager.attach_projects(self.state.projects)
        self._persist()
        await self.broadcast_snapshot()

        if was_active:
            await self.manager.restart(updated)
            await self.broadcast_snapshot()

        return self.serialize_project(updated)

    async def remove_project(self, project_id: str) -> None:
        project = self.get_project(project_id)
        await self.manager.stop(project)
        self.state.projects = [item for item in self.state.projects if item.id != project_id]
        self.preview_state.pop(project_id, None)
        if self.state.dashboard.expanded_project_id == project_id:
            self.state.dashboard.expanded_project_id = None
        self.manager.attach_projects(self.state.projects)
        self._persist()
        await self.broadcast_snapshot()

    async def set_panel(self, project_id: str, panel: Dict[str, Any]) -> Dict[str, Any]:
        project = self.get_project(project_id)
        project.panel = PanelLayout.from_dict(panel)
        self._persist()
        await self.broadcast_snapshot()
        return self.serialize_project(project)

    async def set_visibility(self, project_id: str, visible: bool) -> Dict[str, Any]:
        project = self.get_project(project_id)
        project.panel.visible = visible
        if not visible and self.state.dashboard.expanded_project_id == project_id:
            self.state.dashboard.expanded_project_id = None
        self._persist()
        await self.broadcast_snapshot()
        return self.serialize_project(project)

    async def set_archived(self, project_id: str, archived: bool) -> Dict[str, Any]:
        project = self.get_project(project_id)
        project.archived = archived
        if archived:
            # Archiving also pulls the panel off the dashboard grid, same as Hide,
            # so an archived project takes no space on the dashboard or the sidebar.
            project.panel.visible = False
            if self.state.dashboard.expanded_project_id == project_id:
                self.state.dashboard.expanded_project_id = None
        self._persist()
        await self.broadcast_snapshot()
        return self.serialize_project(project)

    async def reset_layout(self) -> Dict[str, Any]:
        for index, project in enumerate(self.state.projects):
            project.panel = PanelLayout(
                x=(index % 2) * 6,
                y=(index // 2) * 10,
                w=6,
                h=12 if project.project_type != ProjectType.TERMINAL else 10,
                visible=True,
                collapsed=False,
            )
        self.state.dashboard.expanded_project_id = None
        self._persist()
        await self.broadcast_snapshot()
        return self.serialize_state()

    async def resolve_ports(self) -> Dict[str, Any]:
        """Reassign any clashing ports to unique free values so projects can run together."""
        reserved = self._reserved_ports()
        used: set[int] = set()
        changed = False
        for project in self.state.projects:
            for port in project.ports:
                if port.value <= 0:
                    continue
                if port.value in used or port.value in reserved:
                    new_value = self._next_free_port(port.value, used | reserved)
                    old_value = port.value
                    port.value = new_value
                    self._update_web_url_port(project, old_value, new_value)
                    changed = True
                used.add(port.value)
        if changed:
            self._persist()
            await self.broadcast_snapshot()
        return self.serialize_state()

    def _reserved_ports(self) -> set[int]:
        reserved: set[int] = set()
        own = os.environ.get("LOCALDECK_PORT", "8900")
        try:
            reserved.add(int(own))
        except (TypeError, ValueError):
            pass
        return reserved

    @staticmethod
    def _next_free_port(start: int, taken: set[int]) -> int:
        candidate = max(int(start), 1024)
        while candidate in taken and candidate < 65535:
            candidate += 1
        return candidate

    @staticmethod
    def _update_web_url_port(project: ProjectConfig, old_port: int, new_port: int) -> None:
        if not project.web_url:
            return
        parsed = urlparse(project.web_url)
        if parsed.port != old_port:
            return
        host = parsed.hostname or "127.0.0.1"
        netloc = f"{host}:{new_port}"
        project.web_url = urlunparse(
            (parsed.scheme or "http", netloc, parsed.path, parsed.params, parsed.query, parsed.fragment)
        )

    async def set_expanded_project(self, project_id: Optional[str]) -> Dict[str, Any]:
        if project_id is not None:
            self.get_project(project_id)
        self.state.dashboard.expanded_project_id = project_id
        self._persist()
        await self.broadcast_snapshot()
        return self.serialize_state()

    async def start_project(self, project_id: str, extra_args: str | None = None) -> Dict[str, Any]:
        project = self.get_project(project_id)
        # Said before the start, not after: the failure a dead venv produces is
        # "bad CPU type in executable", which names neither the venv nor the
        # project. This line sits directly above it in the same terminal.
        warnings = env_check.warnings_for(
            env_check.status(project.working_directory), project.working_directory
        )
        if warnings:
            self.manager.note(project.id, [f"localdeck: {line}" for line in warnings])
        await self.manager.start(project, extra_args=extra_args)
        await self.broadcast_snapshot()
        return self.serialize_project(project)

    async def stop_project(self, project_id: str) -> Dict[str, Any]:
        project = self.get_project(project_id)
        await self.manager.stop(project)
        await self.broadcast_snapshot()
        return self.serialize_project(project)

    async def restart_project(self, project_id: str,
                              extra_args: str | None = None) -> Dict[str, Any]:
        project = self.get_project(project_id)
        await self.manager.restart(project, extra_args=extra_args)
        await self.broadcast_snapshot()
        return self.serialize_project(project)

    async def clear_logs(self, project_id: str) -> Dict[str, Any]:
        await self.manager.clear_logs(project_id)
        await self.broadcast_snapshot()
        return self.serialize_project(self.get_project(project_id))

    async def check_conformance(self, project_id: str) -> Dict[str, Any]:
        project = self.get_project(project_id)
        scorecard = await asyncio.to_thread(standards.evaluate, project.working_directory)
        return {"project_id": project_id, "scorecard": scorecard}

    async def apply_conformance(self, project_id: str) -> Dict[str, Any]:
        project = self.get_project(project_id)
        scorecard = await asyncio.to_thread(standards.evaluate, project.working_directory)
        plan = standards.generate_plan(project.name, project.working_directory, scorecard)

        target = Path(project.working_directory)
        if not target.is_dir():
            raise ProcessManagerError(f"Working directory does not exist: {project.working_directory}")

        plan_path = target / "CONTROL_DECK_STANDARDS.md"

        def write() -> None:
            plan_path.write_text(plan, encoding="utf-8")

        await asyncio.to_thread(write)
        return {
            "project_id": project_id,
            "scorecard": scorecard,
            "written": str(plan_path),
        }

    async def start_all(self) -> Dict[str, Any]:
        for project in self.state.projects:
            with contextlib.suppress(ProcessManagerError):
                await self.manager.start(project)
        await self.broadcast_snapshot()
        return self.serialize_state()

    async def stop_all(self) -> Dict[str, Any]:
        for project in self.state.projects:
            await self.manager.stop(project)
        await self.broadcast_snapshot()
        return self.serialize_state()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.connections.add(websocket)
        await websocket.send_text(
            json.dumps(
                {
                    "event": "snapshot",
                    "payload": self.serialize_state(),
                }
            )
        )

    def disconnect(self, websocket: WebSocket) -> None:
        self.connections.discard(websocket)

    async def broadcast_snapshot(self) -> None:
        await self._emit_event("snapshot", self.serialize_state())

    async def _emit_event(self, event: str, payload: Dict[str, Any]) -> None:
        if not self.connections:
            return
        message = json.dumps({"event": event, "payload": payload})
        # Send with a per-client timeout so one stuck/dead client can never block
        # the event loop (which previously froze the whole app after a while).
        targets = list(self.connections)
        results = await asyncio.gather(
            *(self._safe_send(ws, message) for ws in targets),
            return_exceptions=True,
        )
        for websocket, ok in zip(targets, results):
            if ok is not True:
                self.connections.discard(websocket)

    @staticmethod
    async def _safe_send(websocket: WebSocket, message: str) -> bool:
        try:
            await asyncio.wait_for(websocket.send_text(message), timeout=5.0)
            return True
        except Exception:
            return False

    async def _heartbeat_loop(self) -> None:
        """Send a tiny beat on a fixed cadence so silence means something.

        A websocket can stop delivering without ever firing "close" — a laptop
        that slept, a network that changed, a tab Chrome froze to save memory.
        The dashboard then looks frozen while every button still works, because
        the buttons are plain HTTP: you press Start, the project really starts,
        and nothing on screen moves. Indistinguishable from a broken button.

        With a beat every HEARTBEAT_SECONDS, a client can tell "nothing has
        happened" from "nothing is arriving" and reconnect itself.
        """
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            if self.connections:
                await self._emit_event("heartbeat", {})

    async def _preview_loop(self) -> None:
        while True:
            for project in self.state.projects:
                if project.project_type in {ProjectType.WEB, ProjectType.HYBRID} and project.web_url:
                    runtime = self.manager.snapshot(project.id)
                    if runtime["status"] in {"Starting", "Running"}:
                        preview = await self._check_url(project.web_url)
                    else:
                        preview = self._default_preview(project)
                    # Only broadcast when something the dashboard shows actually
                    # changed. `checked_at` differs on every probe, so comparing
                    # the whole dict used to push a "project-preview" event to
                    # every open tab every 2.5s per running web project, each
                    # one re-rendering the entire dashboard for nothing.
                    previous = self.preview_state.get(project.id)
                    changed = previous is None or any(
                        preview.get(key) != previous.get(key)
                        for key in ("reachable", "last_error", "serving", "html_cacheable")
                    )
                    self.preview_state[project.id] = preview
                    if preview.get("reachable"):
                        await self._report_build_problems(project, preview)
                    if changed:
                        await self._emit_event(
                            "project-preview",
                            {
                                "project_id": project.id,
                                "preview": preview,
                            },
                        )
                else:
                    self.preview_state[project.id] = self._default_preview(project)
            await asyncio.sleep(2.5)

    async def _report_build_problems(
        self, project: ProjectConfig, preview: Dict[str, Any]
    ) -> None:
        """Say it in the project's own terminal, once, when it starts serving.

        Only for apps actually handing out a build from disk: a dev server
        compiles every request, so none of this can apply to one and warning
        about it would just teach him to ignore warnings.
        """
        if preview.get("serving") != "built":
            problems: list[str] = []
        else:
            problems = build_check.warnings_for(
                build_check.status(project.working_directory)
            )
            if preview.get("html_cacheable"):
                problems.append(
                    "localdeck: this app serves its HTML without 'Cache-Control: no-store', "
                    "so a browser may keep showing an older build after a rebuild. localdeck "
                    "adds a cache-busting build id to the URL it opens, but anything opening the "
                    "plain URL can still be served the old page."
                )

        previous = self._reported_build_problems.get(project.id, [])
        if problems == previous:
            return
        self._reported_build_problems[project.id] = problems

        # Each line is written to the terminal once, the first time it is true.
        # A problem that has been fixed leaves its line in the scrollback (it did
        # happen) but stops being reported.
        fresh = [line for line in problems if line not in previous]
        if fresh:
            self.manager.note(project.id, fresh)
        # The panel reads build status out of the snapshot, so a change here — a
        # rebuild that fixed it included — has to be broadcast or an open page
        # keeps showing a warning that is no longer true.
        await self.broadcast_snapshot()

    async def _check_url(self, url: str) -> Dict[str, Any]:
        def fetch() -> Dict[str, Any]:
            request = urllib.request.Request(url, method="GET")
            try:
                with urllib.request.urlopen(request, timeout=2.0) as response:
                    # Read a little of the body. It tells us, definitively, whether
                    # this app compiles every request (a dev server, which cannot
                    # serve a stale build) or hands out a bundle built earlier (which
                    # can) — and whether it lets that HTML be cached, which is what
                    # turns a rebuild into a version the browser never picks up.
                    head = response.read(4096)
                    return {
                        "reachable": 200 <= response.status < 500,
                        "last_error": None,
                        "serving": build_check.classify_html(head),
                        "html_cacheable": build_check.html_is_cacheable(
                            response.headers.get("Cache-Control")
                        ),
                    }
            except urllib.error.URLError as exc:
                return {
                    "reachable": False,
                    "last_error": str(exc.reason),
                    "serving": None,
                    "html_cacheable": None,
                }
            except Exception as exc:  # pragma: no cover
                return {
                    "reachable": False,
                    "last_error": str(exc),
                    "serving": None,
                    "html_cacheable": None,
                }

        result = await asyncio.to_thread(fetch)
        result["checked_at"] = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat() + "Z"
        return result

    def _persist(self) -> None:
        self.store.save(self.state)

    def _sort_projects(self) -> None:
        self.state.projects.sort(key=lambda project: (project.order, project.name.lower()))

    async def reorder_projects(self, ordered_ids: list[str]) -> Dict[str, Any]:
        """Apply a new sidebar order (list of project ids) and persist it."""
        position = {pid: index for index, pid in enumerate(ordered_ids)}
        # Unknown ids keep their relative place at the end.
        self.state.projects.sort(key=lambda p: position.get(p.id, len(position)))
        for index, project in enumerate(self.state.projects):
            project.order = index
        self._persist()
        await self.broadcast_snapshot()
        return self.serialize_state()

    def _ensure_category(self, category: Optional[str]) -> None:
        name = (category or "").strip()
        if name and name not in self.state.dashboard.categories:
            self.state.dashboard.categories.append(name)

    async def add_category(self, name: str) -> Dict[str, Any]:
        clean = (name or "").strip()
        if clean and clean not in self.state.dashboard.categories:
            self.state.dashboard.categories.append(clean)
            self._persist()
            await self.broadcast_snapshot()
        return self.serialize_state()

    async def remove_category(self, name: str) -> Dict[str, Any]:
        clean = (name or "").strip()
        if clean in self.state.dashboard.categories:
            self.state.dashboard.categories = [
                c for c in self.state.dashboard.categories if c != clean
            ]
            # Projects in the removed category become uncategorized (show under "All").
            for project in self.state.projects:
                if project.category == clean:
                    project.category = "General"
            self._persist()
            await self.broadcast_snapshot()
        return self.serialize_state()

    async def set_project_category(self, project_id: str, category: str) -> Dict[str, Any]:
        project = self.get_project(project_id)
        project.category = (category or "General").strip() or "General"
        self._ensure_category(project.category)
        self._persist()
        await self.broadcast_snapshot()
        return self.serialize_project(project)

    def _generate_project_id(self, seed: str) -> str:
        base = re.sub(r"[^a-z0-9]+", "-", seed.lower()).strip("-") or "project"
        candidate = base
        counter = 2
        existing_ids = {project.id for project in self.state.projects}
        while candidate in existing_ids:
            candidate = f"{base}-{counter}"
            counter += 1
        return candidate

    @staticmethod
    def _default_preview(project: ProjectConfig) -> Dict[str, Any]:
        return {
            "reachable": False,
            "last_error": None if project.web_url else "No web URL configured.",
            "checked_at": None,
            "serving": None,
            "html_cacheable": None,
        }
