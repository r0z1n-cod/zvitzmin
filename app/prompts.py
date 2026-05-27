from __future__ import annotations

from pathlib import Path

from app.settings import PROMPTS_DIR


def load_validation_prompt() -> str:
    return (PROMPTS_DIR / "photo_validation_prompt.md").read_text(encoding="utf-8")
