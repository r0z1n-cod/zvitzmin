from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PIL import Image
from PIL.ExifTags import TAGS


def get_exif_datetime(image_path: Path) -> str:
    try:
        with Image.open(image_path) as image:
            exif_data = image.getexif()
            if not exif_data:
                return ""
            for key, value in exif_data.items():
                tag_name = TAGS.get(key, key)
                if tag_name in {"DateTimeOriginal", "DateTime", "DateTimeDigitized"}:
                    text = str(value).strip()
                    if text:
                        return text
    except Exception:
        return ""
    return ""


def build_ai_image(image_path: Path) -> Path:
    ai_path = image_path.with_name(f"{image_path.stem}_ai.jpg")
    try:
        with Image.open(image_path) as image:
            converted = image.convert("RGB")
            converted.thumbnail((1280, 1280))
            converted.save(ai_path, format="JPEG", quality=78, optimize=True)
    except Exception:
        return image_path
    return ai_path
