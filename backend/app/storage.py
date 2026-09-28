from __future__ import annotations

import json
from pathlib import Path

from .models import AppState, default_state, migrate_state


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> AppState:
        if not self.path.exists():
            state = default_state()
            self.save(state)
            return state

        raw = json.loads(self.path.read_text(encoding="utf-8"))
        state = AppState.from_dict(raw)
        if migrate_state(state):
            self.save(state)
        return state

    def save(self, state: AppState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        tmp_path.replace(self.path)

