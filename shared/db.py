from __future__ import annotations

import json
import os
from datetime import datetime, timezone

DB_PATH = os.path.expanduser("~/job-agent/data/last_run.json")


def _load() -> dict:
    if not os.path.exists(DB_PATH):
        return {}
    with open(DB_PATH) as f:
        return json.load(f)


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with open(DB_PATH, "w") as f:
        json.dump(data, f, indent=2)


def get_last_run(agent_name: str) -> datetime | None:
    data = _load()
    ts = data.get(agent_name)
    if ts is None:
        return None
    return datetime.fromisoformat(ts)


def set_last_run(agent_name: str) -> None:
    data = _load()
    data[agent_name] = datetime.now(timezone.utc).isoformat()
    _save(data)
