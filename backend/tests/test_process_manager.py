from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
import tempfile
import unittest

from app.models import PanelLayout, ProjectConfig, ProjectType
from app.process_manager import ProcessManager, ProcessManagerError, shell_flags



def _process_gone(pid: int) -> bool:
    """True once ``pid`` no longer runs.

    A killed grandchild is re-parented to the init process, and until init reaps
    it the pid is still in the process table as a zombie: dead, but ``kill(pid, 0)``
    succeeds. macOS reaps at once; some containers never do. Count a zombie as gone.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                           capture_output=True, text=True).stdout.strip()
    return state == "" or state.startswith("Z")

class ProcessManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.events = []
        self.manager = ProcessManager(self._emit)
        self.temp_dir = tempfile.TemporaryDirectory()

    async def asyncTearDown(self) -> None:
        for managed in list(self.manager._processes.values()):  # noqa: SLF001
            if managed.is_active:
                await self.manager.stop(managed.project, timeout=2.0)
        self.temp_dir.cleanup()

    async def _emit(self, event: str, payload: dict) -> None:
        self.events.append((event, payload))

    def _project(self, command: str, name: str = "Sample") -> ProjectConfig:
        return ProjectConfig(
            id=name.lower(),
            name=name,
            working_directory=self.temp_dir.name,
            start_command=command,
            project_type=ProjectType.TERMINAL,
            panel=PanelLayout(),
        )

    async def _wait_for(self, predicate, timeout: float = 5.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if predicate():
                return
            await asyncio.sleep(0.05)
        self.fail("Timed out waiting for condition.")

    async def test_start_streams_logs_and_stop_transitions_to_stopped(self) -> None:
        command = "python3 -c 'import time; print(\"booted\", flush=True); time.sleep(30)'"
        project = self._project(command, name="Boot")
        self.manager.attach_projects([project])

        await self.manager.start(project)
        await self._wait_for(
            lambda: "booted" in "\n".join(self.manager.snapshot(project.id)["logs"])
        )

        runtime = self.manager.snapshot(project.id)
        self.assertIn(runtime["status"], {"Starting", "Running"})
        self.assertIn("booted", "\n".join(runtime["logs"]))

        await self.manager.stop(project)
        await self._wait_for(lambda: self.manager.snapshot(project.id)["status"] == "Stopped")
        self.assertEqual(self.manager.snapshot(project.id)["status"], "Stopped")

    async def test_rejects_duplicate_start(self) -> None:
        command = "python3 -c 'import time; time.sleep(30)'"
        project = self._project(command, name="Duplicate")
        self.manager.attach_projects([project])

        await self.manager.start(project)
        with self.assertRaises(ProcessManagerError):
            await self.manager.start(project)

    async def test_invalid_working_directory_marks_project_failed(self) -> None:
        project = ProjectConfig(
            id="broken",
            name="Broken",
            working_directory="/path/that/does/not/exist",
            start_command="python3 -c 'print(1)'",
            project_type=ProjectType.TERMINAL,
        )
        self.manager.attach_projects([project])

        with self.assertRaises(ProcessManagerError):
            await self.manager.start(project)

        self.assertEqual(self.manager.snapshot(project.id)["status"], "Failed")

    async def test_zsh_without_startup_files_starts_without_the_setup_wizard(self) -> None:
        # A fresh account has no ~/.zshrc. An interactive zsh then opens its
        # first-run wizard and waits for a key forever, so nothing ever starts.
        with tempfile.TemporaryDirectory() as empty_home:
            project = self._project("echo ready", name="FreshHome")
            project.environment = {"HOME": empty_home}
            self.manager.attach_projects([project])
            await self.manager.start(project)
            await self._wait_for(lambda: self.manager.snapshot(project.id)["status"] == "Stopped")
            self.assertIn("ready", "\n".join(self.manager.snapshot(project.id)["logs"]))

    def test_shell_flags(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            self.assertEqual(shell_flags("/bin/zsh", {"HOME": home}), "-lc")
            open(os.path.join(home, ".zshrc"), "w").close()
            self.assertEqual(shell_flags("/bin/zsh", {"HOME": home}), "-ilc")
            self.assertEqual(shell_flags("/bin/bash", {"HOME": home}), "-ilc")

    async def test_stop_kills_child_processes(self) -> None:
        child_program = (
            "import subprocess, sys, time; "
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
            "print(child.pid, flush=True); "
            "time.sleep(30)"
        )
        command = f"python3 -c {shlex.quote(child_program)}"
        project = self._project(command, name="Child")
        self.manager.attach_projects([project])

        await self.manager.start(project)
        await self._wait_for(
            lambda: any(
                line.strip().isdigit()
                for line in self.manager.snapshot(project.id)["logs"]
            )
        )

        logs = self.manager.snapshot(project.id)["logs"]
        child_pid = int(next(line for line in logs if line.strip().isdigit()).strip())

        await self.manager.stop(project)
        await self._wait_for(lambda: self.manager.snapshot(project.id)["status"] == "Stopped")

        await self._wait_for(lambda: _process_gone(child_pid))
        self.assertTrue(_process_gone(child_pid))

    async def test_fast_output_is_batched_into_few_log_broadcasts(self) -> None:
        # Every "project-log" event carries the whole log buffer, so a process that
        # prints thousands of lines quickly must not produce thousands (or even
        # hundreds) of broadcasts. Output arriving within ~100 ms is read as one burst.
        program = "import sys\nfor i in range(20000): sys.stdout.write(f'line {i}\\n')\nsys.stdout.flush()"
        command = f"python3 -c {shlex.quote(program)}"
        project = self._project(command, name="Burst")
        self.manager.attach_projects([project])

        await self.manager.start(project)
        await self._wait_for(
            lambda: self.manager.snapshot(project.id)["status"] in {"Stopped", "Failed"},
            timeout=20.0,
        )

        logs = self.manager.snapshot(project.id)["logs"]
        self.assertEqual(len(logs), 2000)
        self.assertEqual(logs[-1], "line 19999")
        # A read boundary can still land inside a line (it always could), so allow a
        # few split entries but require the buffer to hold the tail of the output.
        expected = {f"line {i}" for i in range(18000, 20000)}
        self.assertGreaterEqual(sum(line in expected for line in logs), 1980)

        log_events = [event for event in self.events if event[0] == "project-log"]
        self.assertLess(len(log_events), 10, f"{len(log_events)} broadcasts for 20000 lines")
        self.assertTrue(all("logs" in payload and "chunk" in payload for _, payload in log_events))

    async def test_log_seq_keeps_counting_after_the_buffer_is_full(self) -> None:
        # The dashboard decides what is new from log_seq, never from len(logs):
        # the buffer is a ring, so its length stops changing at maxlen while a
        # long-running project keeps printing. Comparing lengths froze the panel
        # terminal on the 2000th line and it never updated again.
        program = (
            "import sys, time\n"
            "for i in range(2100): sys.stdout.write(f'line {i}\\n')\n"
            "sys.stdout.flush()\n"
            "time.sleep(0.5)\n"
            "print('after-the-cap', flush=True)\n"
            "time.sleep(30)\n"
        )
        command = f"python3 -c {shlex.quote(program)}"
        project = self._project(command, name="Seq")
        self.manager.attach_projects([project])

        await self.manager.start(project)
        await self._wait_for(
            lambda: len(self.manager.snapshot(project.id)["logs"]) == 2000, timeout=20.0
        )
        filled = self.manager.snapshot(project.id)
        self.assertGreaterEqual(filled["log_seq"], 2100)

        await self._wait_for(
            lambda: "after-the-cap" in self.manager.snapshot(project.id)["logs"], timeout=20.0
        )
        after = self.manager.snapshot(project.id)
        self.assertEqual(len(after["logs"]), len(filled["logs"]))  # length cannot move
        self.assertGreater(after["log_seq"], filled["log_seq"])  # the counter still does

        # Every project-log broadcast carries the counter for the lines it ships.
        log_events = [payload for event, payload in self.events if event == "project-log"]
        self.assertTrue(log_events)
        self.assertTrue(all("log_seq" in payload for payload in log_events))
        seqs = [payload["log_seq"] for payload in log_events]
        self.assertEqual(seqs, sorted(seqs))

        await self.manager.clear_logs(project.id)
        cleared = self.manager.snapshot(project.id)
        self.assertEqual(cleared["logs"], [])
        self.assertEqual(cleared["log_seq"], 0)

    async def test_log_seq_counts_every_line_the_manager_writes(self) -> None:
        # Status lines the manager itself appends ("$ command", errors) must be
        # counted too, or the terminal would silently skip them.
        project = self._project("python3 -c 'print(1)'", name="Counted")
        self.manager.attach_projects([project])
        self.assertEqual(self.manager.snapshot(project.id)["log_seq"], 0)

        await self.manager.start(project)
        await self._wait_for(lambda: self.manager.snapshot(project.id)["status"] == "Stopped")

        snapshot = self.manager.snapshot(project.id)
        self.assertEqual(snapshot["log_seq"], len(snapshot["logs"]))
        self.assertTrue(snapshot["logs"][0].startswith("$ "))


if __name__ == "__main__":
    unittest.main()
