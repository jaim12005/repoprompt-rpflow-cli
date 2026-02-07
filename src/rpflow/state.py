from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


STATE_PATH = Path.home() / ".config" / "rpflow" / "state.json"


@dataclass
class RPState:
    last_window: Optional[int] = None
    last_tab: Optional[str] = None
    last_workspace: Optional[str] = None
    updated_at: Optional[str] = None


def load_state() -> RPState:
    try:
        if not STATE_PATH.exists():
            return RPState()
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return RPState(
            last_window=data.get("last_window"),
            last_tab=data.get("last_tab"),
            last_workspace=data.get("last_workspace"),
            updated_at=data.get("updated_at"),
        )
    except Exception:
        return RPState()


def save_state(state: RPState) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_window": state.last_window,
        "last_tab": state.last_tab,
        "last_workspace": state.last_workspace,
        "updated_at": datetime.now().astimezone().isoformat(),
    }
    STATE_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
