from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


class ProjectType(str, Enum):
    TERMINAL = "terminal"
    WEB = "web"
    HYBRID = "hybrid"


class ProjectStatus(str, Enum):
    STOPPED = "Stopped"
    STARTING = "Starting"
    RUNNING = "Running"
    STOPPING = "Stopping"
    FAILED = "Failed"


@dataclass
class PortMapping:
    name: str = "Port"
    value: int = 0
    env: str = ""

    @classmethod
    def from_dict(cls, raw: Optional[Dict[str, Any]]) -> "PortMapping":
        raw = raw or {}
        return cls(
            name=str(raw.get("name") or "Port"),
            value=int(raw.get("value") or 0),
            env=str(raw.get("env") or ""),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PanelLayout:
    x: int = 0
    y: int = 0
    w: int = 6
    h: int = 10
    visible: bool = True
    collapsed: bool = False

    @classmethod
    def from_dict(cls, raw: Optional[Dict[str, Any]]) -> "PanelLayout":
        raw = raw or {}
        return cls(
            x=int(raw.get("x", 0)),
            y=int(raw.get("y", 0)),
            w=int(raw.get("w", 6)),
            h=int(raw.get("h", 10)),
            visible=bool(raw.get("visible", True)),
            collapsed=bool(raw.get("collapsed", False)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


DEFAULT_CATEGORIES = ["Demo"]

# The repository root. The bundled demo projects live under examples/, so a fresh
# clone has something to start, stop and preview before any real project is added.
REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = REPO_ROOT / "examples"


@dataclass
class DashboardState:
    expanded_project_id: Optional[str] = None
    categories: List[str] = field(default_factory=lambda: list(DEFAULT_CATEGORIES))

    @classmethod
    def from_dict(cls, raw: Optional[Dict[str, Any]]) -> "DashboardState":
        raw = raw or {}
        categories = raw.get("categories")
        if not categories:
            categories = list(DEFAULT_CATEGORIES)
        return cls(
            expanded_project_id=raw.get("expanded_project_id"),
            categories=[str(name) for name in categories],
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ProjectConfig:
    id: str
    name: str
    working_directory: str
    start_command: str
    stop_command: Optional[str] = None
    project_type: ProjectType = ProjectType.TERMINAL
    web_url: Optional[str] = None
    environment: Dict[str, str] = field(default_factory=dict)
    startup_timeout: int = 15
    auto_start: bool = False
    category: str = "General"
    hide_terminal: bool = False
    order: int = 0
    archived: bool = False
    ports: List[PortMapping] = field(default_factory=list)
    panel: PanelLayout = field(default_factory=PanelLayout)
    # An optional SECOND way to start this project, shown as an extra button.
    #
    # Some projects have one alternate mode that is a real choice rather than a
    # setting: a web app can start normally, or start with a public tunnel so it
    # is reachable from a phone. Making that a config file edit means it is not
    # really available; making it a second project means two entries fighting
    # over the same ports. A labelled button that passes extra arguments to the
    # same start command is the honest shape.
    #
    #   "alt_start": {"label": "Phone", "args": "--tunnel",
    #                 "hint": "also opens a public tunnel"}
    alt_start: Optional[Dict[str, Any]] = None

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "ProjectConfig":
        return cls(
            id=str(raw["id"]),
            name=str(raw["name"]),
            working_directory=str(raw["working_directory"]),
            start_command=str(raw["start_command"]),
            stop_command=raw.get("stop_command"),
            project_type=ProjectType(raw.get("project_type", ProjectType.TERMINAL.value)),
            web_url=raw.get("web_url"),
            environment=dict(raw.get("environment") or {}),
            startup_timeout=int(raw.get("startup_timeout", 15)),
            auto_start=bool(raw.get("auto_start", False)),
            category=str(raw.get("category") or "General"),
            hide_terminal=bool(raw.get("hide_terminal", False)),
            order=int(raw.get("order", 0)),
            archived=bool(raw.get("archived", False)),
            ports=[PortMapping.from_dict(item) for item in (raw.get("ports") or [])],
            panel=PanelLayout.from_dict(raw.get("panel")),
            alt_start=(dict(raw["alt_start"]) if raw.get("alt_start") else None),
        )

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["project_type"] = self.project_type.value
        return data

    def env_with_ports(self) -> Dict[str, str]:
        """Project environment plus each port exposed as its env var."""
        merged = dict(self.environment)
        for port in self.ports:
            if port.env:
                merged[port.env] = str(port.value)
        return merged


@dataclass
class AppState:
    version: int = 1
    dashboard: DashboardState = field(default_factory=DashboardState)
    projects: List[ProjectConfig] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "AppState":
        return cls(
            version=int(raw.get("version", 1)),
            dashboard=DashboardState.from_dict(raw.get("dashboard")),
            projects=[ProjectConfig.from_dict(item) for item in raw.get("projects", [])],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "dashboard": self.dashboard.to_dict(),
            "projects": [project.to_dict() for project in self.projects],
        }


def default_projects() -> List[ProjectConfig]:
    """Three small demo projects that ship with the repository."""
    return [
        ProjectConfig(
            id="demo_web",
            name="Demo web app",
            working_directory=str(EXAMPLES_DIR / "demo_web"),
            start_command="python3 server.py",
            project_type=ProjectType.WEB,
            web_url="http://127.0.0.1:5601",
            category="Demo",
            ports=[PortMapping(name="Web UI", value=5601, env="FRONTEND_PORT")],
            # Demonstrates the alternate start: with --share the server prints a
            # stand-in "public" address, which the Share button picks up and shows.
            alt_start={"label": "Share", "args": "--share",
                       "hint": "prints a shareable address (simulated tunnel)",
                       "open_url_pattern": r"http://127\.0\.0\.1:\d+/shared\S*"},
            panel=PanelLayout(x=0, y=0, w=7, h=14, visible=True, collapsed=False),
        ),
        ProjectConfig(
            id="demo_worker",
            name="Demo worker",
            working_directory=str(EXAMPLES_DIR / "demo_worker"),
            start_command="python3 worker.py",
            project_type=ProjectType.TERMINAL,
            category="Demo",
            panel=PanelLayout(x=7, y=0, w=5, h=14, visible=True, collapsed=False),
        ),
        ProjectConfig(
            id="demo_task",
            name="Demo one-shot task",
            working_directory=str(EXAMPLES_DIR / "demo_task"),
            start_command="python3 task.py",
            project_type=ProjectType.TERMINAL,
            category="Demo",
            panel=PanelLayout(x=7, y=14, w=5, h=10, visible=True, collapsed=False),
        ),
    ]


def default_state() -> AppState:
    return AppState(version=STATE_VERSION, projects=default_projects())


# Bump this, and add a step to migrate_state, whenever the saved format changes.
STATE_VERSION = 1


def migrate_state(state: AppState) -> bool:
    """Bring an older saved state up to date. Returns True if anything changed.

    Safe by design: a migration step may only fill in what is missing or rewrite
    an exact known legacy value, so a user's own edits are never clobbered.
    """
    changed = False

    # Seed a persisted sidebar order from the current list order.
    if state.projects and not any(p.order for p in state.projects):
        for index, project in enumerate(state.projects):
            if project.order != index:
                project.order = index
                changed = True

    # Seed default categories only on a truly empty list, so the user can freely
    # add and remove category tabs without them being forced back.
    if not state.dashboard.categories:
        state.dashboard.categories = list(DEFAULT_CATEGORIES)
        changed = True

    if state.version != STATE_VERSION:
        state.version = STATE_VERSION
        changed = True

    return changed
