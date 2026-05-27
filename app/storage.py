from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.settings import PHOTOS_DIR, REPORTS_DIR


def build_photo_path(user_id: int, inspection_code: str, step_code: str, original_name: str | None) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = Path(original_name or "photo.jpg").suffix or ".jpg"
    date_part = datetime.now().strftime("%Y-%m-%d")
    folder = PHOTOS_DIR / date_part / inspection_code
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{stamp}_user{user_id}_{step_code}{suffix}"


def build_report_path(user_id: int, inspection_code: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    date_part = datetime.now().strftime("%Y-%m-%d")
    folder = REPORTS_DIR / date_part
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{stamp}_user{user_id}_{inspection_code}.md"


def save_report(report_path: Path, content: str) -> None:
    report_path.write_text(content, encoding="utf-8")
