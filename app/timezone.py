from __future__ import annotations

from zoneinfo import ZoneInfo
from zoneinfo import ZoneInfoNotFoundError


def get_timezone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        if timezone_name == "Europe/Kiev":
            return ZoneInfo("Europe/Kyiv")
        raise
