from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.settings import BASE_DIR


CONFIG_PATH = BASE_DIR / "config" / "shift_plan.json"


def load_shift_plan() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
