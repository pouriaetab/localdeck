from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class PortPayload(BaseModel):
    name: str = "Port"
    value: int = 0
    env: str = ""


class PanelPayload(BaseModel):
    x: int = 0
    y: int = 0
    w: int = 6
    h: int = 10
    visible: bool = True
    collapsed: bool = False


class ProjectPayload(BaseModel):
    id: Optional[str] = None
    name: str
    working_directory: str
    start_command: str
    stop_command: Optional[str] = None
    project_type: str = "terminal"
    web_url: Optional[str] = None
    environment: Dict[str, str] = Field(default_factory=dict)
    startup_timeout: int = 15
    auto_start: bool = False
    category: str = "General"
    hide_terminal: bool = False
    archived: bool = False
    ports: List[PortPayload] = Field(default_factory=list)
    panel: PanelPayload = Field(default_factory=PanelPayload)
    # A project's second, labelled way to start (for example "Phone"). It has
    # to be here, not only in models.py: this payload is what a save from the
    # Edit dialog sends, and a field missing from it is silently dropped. One
    # edit of the project and the button would have disappeared with no error.
    alt_start: Optional[Dict[str, Any]] = None


class ScheduleActionPayload(BaseModel):
    action: str  # pause | resume | run | stop


class ReschedulePayload(BaseModel):
    hour: int = 9
    minute: int = 0
    weekdays: List[int] = Field(default_factory=lambda: [1, 2, 3, 4, 5])


class ReorderPayload(BaseModel):
    order: List[str] = Field(default_factory=list)


class CategoryPayload(BaseModel):
    name: str


class SetCategoryPayload(BaseModel):
    category: str


class VisibilityPayload(BaseModel):
    visible: bool


class ArchivePayload(BaseModel):
    archived: bool


class ExpandedPayload(BaseModel):
    project_id: Optional[str] = None


class StartPayload(BaseModel):
    extra_args: Optional[str] = None

