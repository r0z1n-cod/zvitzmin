from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
PROMPTS_DIR = BASE_DIR / "prompts"
DATA_DIR = BASE_DIR / "data"
PHOTOS_DIR = DATA_DIR / "photos"
REPORTS_DIR = DATA_DIR / "reports"
FEEDBACK_DIR = DATA_DIR / "feedback"


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_report_chat_id: str
    openai_api_key: str
    openai_model: str
    business_name: str
    report_language: str
    timezone: str


def load_settings() -> Settings:
    load_dotenv(BASE_DIR / ".env")

    settings = Settings(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_report_chat_id=os.getenv("TELEGRAM_REPORT_CHAT_ID", "").strip(),
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip(),
        business_name=os.getenv("BUSINESS_NAME", "My Restaurant").strip(),
        report_language=os.getenv("REPORT_LANGUAGE", "uk").strip(),
        timezone=os.getenv("TIMEZONE", "Europe/Kiev").strip(),
    )

    if not settings.telegram_bot_token:
        raise ValueError("Missing TELEGRAM_BOT_TOKEN in .env")
    if not settings.openai_api_key:
        raise ValueError("Missing OPENAI_API_KEY in .env")

    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    return settings
