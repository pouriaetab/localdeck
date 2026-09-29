from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import errno
import os
import pty
import select
import shlex
import signal
import subprocess
import time
from collections import deque
from pathlib import Path
from urllib.parse import urlparse
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Deque, Dict, Optional

from .models import ProjectConfig, ProjectStatus

Emitter = Callable[[str, Dict[str, Any]], Awaitable[None]]


# zsh reads these from $ZDOTDIR (or $HOME). With none of them present, an
# INTERACTIVE zsh runs zsh-newuser-install: a full-screen first-run wizard that
# waits for a keypress forever. A fresh Linux account (a CI runner, a new user)
# has none, so `zsh -ilc cmd` hangs there and the project never starts. -i only
# exists to load ~/.zshrc; when there is no startup file there is nothing to
# load, so drop it.
ZSH_STARTUP_FILES = (".zshenv", ".zprofile", ".zshrc", ".zlogin")


def shell_flags(shell: str, env: Dict[str, str]) -> str:
    """The flags to start a project's command with: "-ilc", or "-lc" (see above)."""
    if os.path.basename(shell) == "zsh":
        home = env.get("ZDOTDIR") or env.get("HOME") or str(Path.home())
        if not any((Path(home) / name).exists() for name in ZSH_STARTUP_FILES):
            return "-lc"
    return "-ilc"


class ProcessManagerError(RuntimeError):
    """Raised when a project cannot be managed as requested."""


class LogBuffer(deque):
    """A bounded log buffer that also counts every line ever appended to it.

    `seq` keeps rising after the buffer is full, which the buffer's own length
    cannot do. The dashboard needs that to tell "one new line" from "nothing
    changed" once a long-running project has printed more than `maxlen` lines —
    comparing lengths made the panel terminal stop updating at that point.
    """

    def __init__(self, maxlen: int) -> None:
        super().__init__(maxlen=maxlen)
        self.seq = 0

    def append(self, item: str) -> None:
        super().append(item)
        self.seq += 1

    def extend(self, items) -> None:
        for item in items:
            self.append(item)

    def clear(self) -> None:
        super().clear()
        self.seq = 0


@dataclass
class ManagedProcess:
    project: ProjectConfig
    process: Optional[subprocess.Popen[bytes]] = None
    master_fd: Optional[int] = None
    status: ProjectStatus = ProjectStatus.STOPPED
    started_at: Optional[str] = None
    stopped_at: Optional[str] = None
    pid: Optional[int] = None
    exit_code: Optional[int] = None
    last_error: Optional[str] = None
    intentional_stop: bool = False
    logs: Deque[str] = field(default_factory=lambda: LogBuffer(maxlen=2000))
    reader_task: Optional[asyncio.Task[None]] = None
    waiter_task: Optional[asyncio.Task[None]] = None
    running_task: Optional[asyncio.Task[None]] = None

    @property
    def is_active(self) -> bool:
        return self.status in {
            ProjectStatus.STARTING,
            ProjectStatus.RUNNING,
            ProjectStatus.STOPPING,
        }


class ProcessManager:
    def __init__(self, emitter: Emitter, log_limit: int = 2000) -> None:
        self._emitter = emitter
        self._log_limit = log_limit
        self._processes: Dict[str, ManagedProcess] = {}

    def attach_projects(self, projects: list[ProjectConfig]) -> None:
        configured_ids = {project.id for project in projects}
        for project in projects:
            managed = self._processes.get(project.id)
            if managed is None:
                self._processes[project.id] = ManagedProcess(
                    project=project,
                    logs=LogBuffer(maxlen=self._log_limit),
                )
            else:
                managed.project = project

        for removed_id in list(self._processes):
            if removed_id not in configured_ids and not self._processes[removed_id].is_active:
                del self._processes[removed_id]

    def snapshot(self, project_id: str) -> Dict[str, Any]:
        managed = self._processes.get(project_id)
        if managed is None:
            return self._empty_runtime()

        return {
            "status": managed.status.value,
            "pid": managed.pid,
            "started_at": managed.started_at,
            "stopped_at": managed.stopped_at,
            "exit_code": managed.exit_code,
            "last_error": managed.last_error,
            "logs": list(managed.logs),
            "log_seq": managed.logs.seq,
        }

    async def start(self, project: ProjectConfig, extra_args: str | None = None) -> Dict[str, Any]:
        managed = self._ensure_process(project)
        if managed.is_active:
            raise ProcessManagerError(f"{project.name} is already {managed.status.value.lower()}.")

        working_dir = os.path.abspath(project.working_directory)
        if not os.path.isdir(working_dir):
            managed.status = ProjectStatus.FAILED
            managed.last_error = f"Working directory does not exist: {working_dir}"
            await self._emit_project_update(project.id)
            raise ProcessManagerError(managed.last_error)

        # Clear this project's assigned ports first, so a leftover/zombie process
        # (e.g. from a previous frozen session) can't hijack the panel or block
        # the port. Only touches THIS project's ports, never localdeck's own.
        await self._free_project_ports(project)

        # Build the full command — optionally append caller-supplied extra args
        # (e.g. "--date 2026-04-20 --dry-run").
        full_command = project.start_command
        if extra_args and extra_args.strip():
            full_command = f"{full_command} {extra_args.strip()}"

        if managed.logs:
            managed.logs.append("")
        managed.logs.append(f"$ {full_command}")
        managed.status = ProjectStatus.STARTING
        managed.last_error = None
        managed.exit_code = None
        managed.intentional_stop = False
        managed.started_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat() + "Z"
        managed.stopped_at = None
        await self._emit_project_update(project.id)

        master_fd, slave_fd = pty.openpty()
        env = os.environ.copy()
        env.update(project.env_with_ports())
        # Make non-interactive launches behave like the user's normal terminal.
        env.setdefault("TERM", "xterm-256color")

        # Use an *interactive* login shell (-i -l) so that ~/.zshrc is sourced
        # (shell_flags drops -i for a zsh with no startup files; see above).
        # Most users configure pyenv/conda/nvm/homebrew PATH inside ~/.zshrc, which a
        # plain `-lc` shell skips — that is a common cause of "command not found"
        # errors when a project starts fine in Terminal but fails here.
        shell_command = os.environ.get("LOCALDECK_SHELL", "/bin/zsh")
        try:
            process = subprocess.Popen(
                [shell_command, shell_flags(shell_command, env), full_command],
                cwd=working_dir,
                env=env,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                start_new_session=True,
                close_fds=True,
            )
        except Exception as exc:
            os.close(master_fd)
            os.close(slave_fd)
            managed.status = ProjectStatus.FAILED
            managed.last_error = str(exc)
            await self._emit_project_update(project.id)
            raise ProcessManagerError(str(exc)) from exc
        finally:
            try:
                os.close(slave_fd)
            except OSError:
                pass

        managed.process = process
        managed.master_fd = master_fd
        managed.pid = process.pid
        managed.reader_task = asyncio.create_task(self._read_output(project.id))
        managed.waiter_task = asyncio.create_task(self._wait_for_exit(project.id))
        managed.running_task = asyncio.create_task(self._mark_running(project.id))
        await self._emit_project_update(project.id)
        return self.snapshot(project.id)

    async def stop(self, project: ProjectConfig, timeout: float = 6.0) -> Dict[str, Any]:
        managed = self._ensure_process(project)
        if not managed.process or not managed.is_active:
            managed.status = ProjectStatus.STOPPED
            await self._emit_project_update(project.id)
            return self.snapshot(project.id)

        managed.intentional_stop = True
        managed.status = ProjectStatus.STOPPING
        await self._emit_project_update(project.id)

        if project.stop_command:
            await self._run_stop_command(project)

        pgid = None
        try:
            pgid = os.getpgid(managed.process.pid)
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass

        try:
            if managed.waiter_task:
                await asyncio.wait_for(asyncio.shield(managed.waiter_task), timeout=timeout)
        except asyncio.TimeoutError:
            if pgid is not None:
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if managed.waiter_task:
                await asyncio.shield(managed.waiter_task)

        # Belt-and-suspenders: force-kill the group again (in case a child
        # re-parented or escaped) and free every port the project declares, so
        # nothing is left running in the background after a stop.
        if pgid is not None:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        await self._free_project_ports(project)

        return self.snapshot(project.id)

    async def restart(self, project: ProjectConfig,
                      extra_args: str | None = None) -> Dict[str, Any]:
        """Stop, then start again — optionally in a different mode.

        `extra_args` exists so a project with an alternate start (see
        ProjectConfig.alt_start) can be switched into that mode while it is
        already running, without the caller hand-rolling stop / sleep / start.
        Doing it here means the port-clearing in start() runs between the two,
        which is the part a hand-rolled version gets wrong: a fixed two-second
        sleep is not always long enough for uvicorn and vite to release their ports,
        and the start then fails with the project left stopped.
        """
        await self.stop(project)
        return await self.start(project, extra_args=extra_args)

    def note(self, project_id: str, lines: list[str]) -> None:
        """Write localdeck's own lines into a project's terminal.

        Used for things the dashboard notices about a project that the project
        itself would never print — a stale build, HTML that can be cached.
        """
        managed = self._processes.get(project_id)
        if managed is None:
            return
        for line in lines:
            managed.logs.append(line)

    async def clear_logs(self, project_id: str) -> Dict[str, Any]:
        managed = self._processes.get(project_id)
        if managed:
            managed.logs.clear()
            await self._emit_project_update(project_id)
        return self.snapshot(project_id)

    async def shutdown(self) -> None:
        active_projects = [
            managed.project
            for managed in self._processes.values()
            if managed.is_active
        ]
        for project in active_projects:
            await self.stop(project, timeout=3.0)

    def _ensure_process(self, project: ProjectConfig) -> ManagedProcess:
        managed = self._processes.get(project.id)
        if managed is None:
            managed = ManagedProcess(project=project, logs=LogBuffer(maxlen=self._log_limit))
            self._processes[project.id] = managed
        else:
            managed.project = project
        return managed

    async def _mark_running(self, project_id: str) -> None:
        await asyncio.sleep(0.35)
        managed = self._processes.get(project_id)
        if not managed or not managed.process:
            return
        if managed.status == ProjectStatus.STARTING and managed.process.poll() is None:
            managed.status = ProjectStatus.RUNNING
            await self._emit_project_update(project_id)

    async def _read_output(self, project_id: str) -> None:
        managed = self._processes[project_id]
        if managed.master_fd is None:
            return

        try:
            while True:
                chunk = await asyncio.to_thread(self._read_burst, managed.master_fd)
                if not chunk:
                    break
                text = chunk.decode(errors="replace")
                for line in text.replace("\r\n", "\n").splitlines():
                    managed.logs.append(line)
                await self._emitter(
                    "project-log",
                    {
                        "project_id": project_id,
                        "chunk": text,
                        "logs": list(managed.logs),
                        "log_seq": managed.logs.seq,
                    },
                )
        finally:
            if managed.master_fd is not None:
                try:
                    os.close(managed.master_fd)
                except OSError:
                    pass
                managed.master_fd = None

    async def _wait_for_exit(self, project_id: str) -> None:
        managed = self._processes[project_id]
        if not managed.process:
            return

        return_code = await asyncio.to_thread(managed.process.wait)
        managed.exit_code = return_code
        managed.pid = None
        managed.process = None
        managed.stopped_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat() + "Z"

        if managed.running_task and not managed.running_task.done():
            managed.running_task.cancel()
        if managed.reader_task:
            with contextlib.suppress(Exception):
                await managed.reader_task

        if managed.intentional_stop:
            managed.status = ProjectStatus.STOPPED
            managed.last_error = None
        elif return_code == 0:
            managed.status = ProjectStatus.STOPPED
            managed.last_error = None
        else:
            managed.status = ProjectStatus.FAILED
            managed.last_error = f"Process exited with code {return_code}."
            managed.logs.append(managed.last_error)

        await self._emit_project_update(project_id)

    async def _free_project_ports(self, project: ProjectConfig) -> None:
        own_port = os.environ.get("LOCALDECK_PORT", "8900")

        # Collect every port the project declares, plus its web URL's port.
        candidates: set[int] = set()
        for mapping in project.ports:
            if mapping.value and str(mapping.value) != str(own_port):
                candidates.add(int(mapping.value))
        web_port = self._extract_port(project.web_url) if project.web_url else None
        if web_port and str(web_port) != str(own_port):
            candidates.add(int(web_port))

        if not candidates:
            return

        managed = self._processes.get(project.id)
        shell = os.environ.get("LOCALDECK_SHELL", "/bin/zsh")
        own_pid = os.getpid()

        def free() -> list[str]:
            killed: list[str] = []
            for port in candidates:
                # One port per query. `lsof -ti tcp:PORT -sTCP:LISTEN` prints only the
                # PIDs listening on that exact port (a bare `-i` would match everything).
                try:
                    result = subprocess.run(
                        [shell, "-lc", f"lsof -ti tcp:{port} -sTCP:LISTEN"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                except Exception:
                    continue
                pids = [p for p in result.stdout.decode(errors="replace").split() if p.strip().isdigit()]
                for pid in pids:
                    if int(pid) == own_pid:
                        continue  # never kill localdeck itself
                    try:
                        os.kill(int(pid), signal.SIGKILL)
                        killed.append(pid)
                    except (ProcessLookupError, ValueError, PermissionError):
                        pass
            return killed

        killed = await asyncio.to_thread(free)
        if killed and managed is not None:
            ports_label = ", ".join(str(p) for p in sorted(candidates))
            managed.logs.append(
                f"Freed ports {ports_label} (killed lingering PIDs: {', '.join(killed)})."
            )
            await self._emit_project_update(project.id)

    @staticmethod
    def _extract_port(url: str) -> Optional[int]:
        try:
            parsed = urlparse(url if "://" in url else f"http://{url}")
            return parsed.port
        except (ValueError, TypeError):
            return None

    async def _run_stop_command(self, project: ProjectConfig) -> None:
        managed = self._processes[project.id]
        command = project.stop_command
        if not command:
            return

        def run_command() -> subprocess.CompletedProcess[bytes]:
            env = os.environ.copy()
            env.update(project.env_with_ports())
            return subprocess.run(  # noqa: S603
                ["/bin/zsh", "-lc", command],
                cwd=project.working_directory,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

        result = await asyncio.to_thread(run_command)
        output = result.stdout.decode(errors="replace").strip()
        if output:
            for line in output.splitlines():
                managed.logs.append(line)
        if result.returncode != 0:
            managed.logs.append(
                f"Stop command exited with code {result.returncode}: {shlex.quote(command)}"
            )

    async def _emit_project_update(self, project_id: str) -> None:
        await self._emitter(
            "project-runtime",
            {
                "project_id": project_id,
                "runtime": self.snapshot(project_id),
            },
        )

    @staticmethod
    def _empty_runtime() -> Dict[str, Any]:
        return {
            "status": ProjectStatus.STOPPED.value,
            "pid": None,
            "started_at": None,
            "stopped_at": None,
            "exit_code": None,
            "last_error": None,
            "logs": [],
            "log_seq": 0,
        }

    @staticmethod
    def _read_chunk(fd: int) -> bytes:
        try:
            return os.read(fd, 4096)
        except OSError as exc:
            if exc.errno in {errno.EIO, errno.EBADF}:
                return b""
            raise

    # Every "project-log" broadcast carries the project's whole log buffer (up to
    # 2000 lines, ~200 KB of JSON) to every open dashboard tab. Emitting one per
    # 4 KB read meant a chatty process could push megabytes per second into each
    # browser tab. Reading in short bursts keeps the payload format identical but
    # batches output that arrives within the same ~100 ms into one broadcast.
    BURST_WINDOW_SECONDS = 0.1
    BURST_MAX_BYTES = 256 * 1024

    @classmethod
    def _read_burst(cls, fd: int) -> bytes:
        """Block for the first chunk, then drain what arrives shortly after it.

        Runs in a worker thread. Returns b"" only when the first read hits EOF.
        """
        chunk = cls._read_chunk(fd)
        if not chunk:
            return chunk
        parts = [chunk]
        total = len(chunk)
        deadline = time.monotonic() + cls.BURST_WINDOW_SECONDS
        while total < cls.BURST_MAX_BYTES:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                ready, _, _ = select.select([fd], [], [], remaining)
            except (OSError, ValueError):
                break
            if not ready:
                break
            more = cls._read_chunk(fd)
            if not more:
                break  # EOF: hand back what we have; the next call ends the loop
            parts.append(more)
            total += len(more)
        return b"".join(parts)
