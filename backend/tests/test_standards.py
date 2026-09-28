from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app import standards
from app.models import (
    DEFAULT_CATEGORIES,
    STATE_VERSION,
    AppState,
    default_state,
    migrate_state,
)


class StandardsTests(unittest.TestCase):
    def _conformant_project(self, root: Path) -> None:
        frontend = root / "frontend"
        frontend.mkdir()
        (frontend / "package.json").write_text(
            json.dumps({"dependencies": {"react": "^18"}, "devDependencies": {"vite": "^5"}}),
            encoding="utf-8",
        )
        (frontend / "main.jsx").write_text("const ws = new WebSocket('ws://127.0.0.1:8000/ws');", encoding="utf-8")
        backend = root / "backend"
        backend.mkdir()
        (backend / "main.py").write_text(
            "from fastapi import FastAPI, WebSocket\napp = FastAPI()\n@app.websocket('/ws')\nasync def ws(s: WebSocket): ...\n",
            encoding="utf-8",
        )
        scripts = root / "scripts"
        scripts.mkdir()
        (scripts / "dev.sh").write_text("#!/bin/sh\necho dev\n", encoding="utf-8")
        (root / "README.md").write_text("# Project\n", encoding="utf-8")
        # The rest of what the standards ask for. These four checks were added
        # after this fixture was first written, which is why "the conformant
        # project" had stopped being conformant and this test failed at HEAD:
        # a fixture has to keep up with the rules it claims to satisfy.
        (root / ".env.example").write_text("PORT=8000\n", encoding="utf-8")
        (root / "run.sh").write_text(
            "#!/bin/sh\ncase \"$1\" in --check) ;; --selftest) pytest ;; --doctor) ;; esac\necho run\n",
            encoding="utf-8",
        )
        (root / "CLAUDE.md").write_text("# Non-negotiables\n", encoding="utf-8")
        tests_dir = root / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_smoke.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    def test_conformant_project_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._conformant_project(root)
            scorecard = standards.evaluate(str(root))
            self.assertEqual(scorecard["overall"], "pass")
            self.assertEqual(scorecard["counts"]["fail"], 0)

    def test_empty_project_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scorecard = standards.evaluate(tmp)
            self.assertEqual(scorecard["overall"], "fail")
            ids = {check["id"] for check in scorecard["checks"]}
            self.assertIn("react_vite", ids)
            self.assertIn("fastapi_ws", ids)

    def test_missing_directory_fails(self) -> None:
        scorecard = standards.evaluate("/path/that/does/not/exist/at/all")
        self.assertEqual(scorecard["overall"], "fail")

    def test_plan_lists_actionable_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scorecard = standards.evaluate(tmp)
            plan = standards.generate_plan("Sample", tmp, scorecard)
            self.assertIn("Standards Fix Request", plan)
            self.assertIn("React + Vite frontend", plan)


class MigrationTests(unittest.TestCase):
    def test_old_state_is_brought_to_current_version(self) -> None:
        state = AppState.from_dict(
            {
                "version": 0,
                "dashboard": {"expanded_project_id": None, "categories": []},
                "projects": [
                    {"id": "api", "name": "API", "working_directory": "/x/api",
                     "start_command": "./run.sh", "project_type": "web",
                     "web_url": "http://127.0.0.1:5173"},
                    {"id": "worker", "name": "Worker", "working_directory": "/x/worker",
                     "start_command": "python3 worker.py", "project_type": "terminal"},
                ],
            }
        )
        self.assertTrue(migrate_state(state))
        self.assertEqual(state.version, STATE_VERSION)
        # An empty category list is seeded; a sidebar order is assigned.
        self.assertEqual(state.dashboard.categories, list(DEFAULT_CATEGORIES))
        self.assertEqual([p.order for p in state.projects], [0, 1])
        # The user's own commands are never rewritten.
        self.assertEqual([p.start_command for p in state.projects],
                         ["./run.sh", "python3 worker.py"])
        # A second pass has nothing left to do.
        self.assertFalse(migrate_state(state))

    def test_default_state_uses_bundled_examples_with_unique_ports(self) -> None:
        state = default_state()
        self.assertEqual(state.version, STATE_VERSION)
        for project in state.projects:
            self.assertTrue(Path(project.working_directory).is_dir(), project.working_directory)
        ports = [pm.value for p in state.projects for pm in p.ports]
        self.assertEqual(len(ports), len(set(ports)))
        web = next(p for p in state.projects if p.web_url)
        self.assertEqual(web.env_with_ports().get("FRONTEND_PORT"), "5601")


if __name__ == "__main__":
    unittest.main()
