from __future__ import annotations

import base64
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from openai import APIError
from openai import OpenAI
from openai import RateLimitError

from app.prompts import load_validation_prompt
from app.settings import Settings


class AIAnalysisError(Exception):
    """User-friendly wrapper for OpenAI request errors."""


def _image_to_data_url(image_path: Path) -> str:
    image_bytes = image_path.read_bytes()
    encoded = base64.b64encode(image_bytes).decode("ascii")
    suffix = image_path.suffix.lower().replace(".", "") or "jpeg"
    mime = "jpeg" if suffix == "jpg" else suffix
    return f"data:image/{mime};base64,{encoded}"


def _parse_json_object(content: str) -> dict[str, Any]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise
        parsed = json.loads(match.group(0))

    if not isinstance(parsed, dict):
        raise json.JSONDecodeError("Expected JSON object", cleaned, 0)
    return parsed


def validate_checkpoint_photo(
    *,
    settings: Settings,
    image_path: Path,
    mode_label: str,
    checkpoint: dict[str, Any],
    received_at: datetime,
    exif_datetime: str,
) -> dict[str, Any]:
    client = OpenAI(api_key=settings.openai_api_key)

    prompt = load_validation_prompt().format(
        business_name=settings.business_name,
        mode_label=mode_label,
        step_title=checkpoint["title"],
        required_subject=checkpoint["required_subject"],
        must_see=", ".join(checkpoint.get("must_see", [])),
        focus=checkpoint.get("focus", ""),
        timezone=settings.timezone,
        received_at=received_at.isoformat(),
        exif_datetime=exif_datetime or "unknown",
    )

    try:
        response = client.responses.create(
            model=settings.openai_model,
            max_output_tokens=220,
            input=[
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": prompt}],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "Перевір це фото для поточного кроку чекліста і поверни тільки JSON.",
                        },
                        {
                            "type": "input_image",
                            "image_url": _image_to_data_url(image_path),
                        },
                    ],
                },
            ],
        )
        content = response.output_text.strip()
        return _parse_json_object(content)
    except json.JSONDecodeError as exc:
        raise AIAnalysisError("ШІ повернув некоректну відповідь. Спробуйте ще раз з іншим фото.") from exc
    except RateLimitError as exc:
        raise AIAnalysisError(
            "OpenAI тимчасово недоступний для цього ключа: вичерпана квота або не налаштований білінг."
        ) from exc
    except APIError as exc:
        raise AIAnalysisError(
            "OpenAI зараз не зміг обробити запит. Спробуйте ще раз трохи пізніше."
        ) from exc
