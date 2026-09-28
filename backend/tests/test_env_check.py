"""The venv check: does it catch a dead environment without crying wolf?"""
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import env_check  # noqa: E402


def thin_macho(path: Path, cpu_type: int) -> None:
    """A single-architecture Mach-O header, little-endian, like a real binary."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xcf\xfa\xed\xfe" + struct.pack("<I", cpu_type) + b"\x00" * 24)
    path.chmod(0o755)


def fat_macho(path: Path, cpu_types) -> None:
    """A universal binary listing several slices, big-endian, as the format is."""
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = b"\xca\xfe\xba\xbe" + struct.pack(">I", len(cpu_types))
    for cpu in cpu_types:
        blob += struct.pack(">I", cpu) + b"\x00" * 16
    path.write_bytes(blob)
    path.chmod(0o755)


def make_venv(root: Path, relative: str, *, home: str, version: str = "3.11.1") -> Path:
    venv = root / relative
    (venv / "bin").mkdir(parents=True, exist_ok=True)
    (venv / "pyvenv.cfg").write_text(
        f"home = {home}\ninclude-system-site-packages = false\nversion = {version}\n"
    )
    return venv


class EnvCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        env_check._cache.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, **kwargs):
        return env_check.status(
            str(self.root), refresh=True, platform="darwin", machine="arm64", **kwargs
        )

    # --- the quiet cases -----------------------------------------------------

    def test_no_venv_at_all_says_nothing(self):
        """run.sh creates one on first run. That is not a fault to report."""
        self.assertIsNone(self.check())

    def test_a_working_venv_produces_no_warning(self):
        home = self.root / "pythonhome"
        (home).mkdir()
        venv = make_venv(self.root, ".venv", home=str(home))
        thin_macho(venv / "bin" / "python3", env_check.CPU_TYPE_ARM64)
        env = self.check()
        self.assertTrue(env["has_usable"])
        self.assertEqual(env_check.warnings_for(env, str(self.root)), [])

    def test_universal_binary_counts_as_usable(self):
        home = self.root / "pythonhome"
        home.mkdir()
        venv = make_venv(self.root, ".venv", home=str(home))
        fat_macho(venv / "bin" / "python3", [env_check.CPU_TYPE_X86_64, env_check.CPU_TYPE_ARM64])
        self.assertTrue(self.check()["has_usable"])

    def test_dead_venv_beside_a_working_one_is_not_reported(self):
        """A dead venv next to a working platform-suffixed one: keep quiet."""
        home = self.root / "pythonhome"
        home.mkdir()
        dead = make_venv(self.root, ".venv", home="/Users/you/miniconda3/bin")
        thin_macho(dead / "bin" / "python3", env_check.CPU_TYPE_X86_64)
        good = make_venv(self.root, ".venv-darwin-arm64", home=str(home))
        thin_macho(good / "bin" / "python3", env_check.CPU_TYPE_ARM64)

        env = self.check()
        self.assertTrue(env["has_usable"])
        self.assertEqual(env_check.warnings_for(env, str(self.root)), [])

    def test_a_non_macho_interpreter_is_left_alone(self):
        """A Linux build, or a wrapper script: not something to guess about."""
        home = self.root / "pythonhome"
        home.mkdir()
        venv = make_venv(self.root, ".venv", home=str(home))
        target = venv / "bin" / "python3"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("#!/bin/sh\nexec python3 \"$@\"\n")
        self.assertTrue(self.check()["has_usable"])

    # --- the failures it exists for ------------------------------------------

    def test_interpreter_home_gone_is_caught(self):
        venv = make_venv(self.root, "venv", home="/Users/you/miniconda3/bin", version="3.10.11")
        thin_macho(venv / "bin" / "python3", env_check.CPU_TYPE_ARM64)
        env = self.check()
        self.assertFalse(env["has_usable"])
        (warning,) = env_check.warnings_for(env, str(self.root))
        self.assertIn("miniconda3", warning)
        self.assertIn("not on this machine any more", warning)

    def test_intel_only_interpreter_is_caught(self):
        home = self.root / "pythonhome"
        home.mkdir()
        venv = make_venv(self.root, ".venv", home=str(home))
        thin_macho(venv / "bin" / "python3", env_check.CPU_TYPE_X86_64)
        env = self.check()
        self.assertFalse(env["has_usable"])
        (warning,) = env_check.warnings_for(env, str(self.root))
        self.assertIn("Intel-only", warning)
        self.assertIn("bad CPU type in executable", warning)

    def test_missing_interpreter_file_is_caught(self):
        make_venv(self.root, "backend/.venv", home="/nowhere/bin")
        env = self.check()
        self.assertFalse(env["has_usable"])
        (warning,) = env_check.warnings_for(env, str(self.root))
        self.assertIn("backend/.venv", warning)

    def test_intel_only_is_not_flagged_on_an_intel_mac(self):
        """The check is about THIS machine, not about Intel binaries in general."""
        home = self.root / "pythonhome"
        home.mkdir()
        venv = make_venv(self.root, ".venv", home=str(home))
        thin_macho(venv / "bin" / "python3", env_check.CPU_TYPE_X86_64)
        env = env_check.status(
            str(self.root), refresh=True, platform="darwin", machine="x86_64"
        )
        self.assertTrue(env["has_usable"])

    # --- the advice ----------------------------------------------------------

    def test_rebuild_hint_names_the_requirements_file(self):
        (self.root / "requirements.txt").write_text("fastapi\n")
        venv = make_venv(self.root, "venv", home="/gone/bin")
        thin_macho(venv / "bin" / "python3", env_check.CPU_TYPE_ARM64)
        (warning,) = env_check.warnings_for(self.check(), str(self.root))
        self.assertIn("rm -rf venv && python3 -m venv venv", warning)
        self.assertIn("venv/bin/pip install -r requirements.txt", warning)

    def test_rebuild_hint_for_a_backend_venv_changes_directory_first(self):
        (self.root / "backend").mkdir()
        (self.root / "backend" / "requirements.txt").write_text("fastapi\n")
        make_venv(self.root, "backend/.venv", home="/gone/bin")
        (warning,) = env_check.warnings_for(self.check(), str(self.root))
        self.assertIn("cd backend && rm -rf .venv", warning)
        self.assertIn(".venv/bin/pip install -r requirements.txt", warning)

    def test_a_project_directory_that_is_gone_is_not_an_error(self):
        self.assertIsNone(
            env_check.status("/definitely/not/here", refresh=True, platform="darwin", machine="arm64")
        )

    def test_results_are_cached_so_the_dashboard_stays_cheap(self):
        home = self.root / "pythonhome"
        home.mkdir()
        venv = make_venv(self.root, ".venv", home=str(home))
        thin_macho(venv / "bin" / "python3", env_check.CPU_TYPE_ARM64)
        first = env_check.status(str(self.root), platform="darwin", machine="arm64")
        # Break it on disk; the cached answer must not change yet.
        (venv / "pyvenv.cfg").write_text("home = /gone/bin\nversion = 3.10.11\n")
        second = env_check.status(str(self.root), platform="darwin", machine="arm64")
        self.assertIs(first, second)
        third = env_check.status(str(self.root), refresh=True, platform="darwin", machine="arm64")
        self.assertFalse(third["has_usable"])


if __name__ == "__main__":
    unittest.main()
