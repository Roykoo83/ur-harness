"""Timezone-aware time helpers."""

from __future__ import annotations

from datetime import datetime


def parse_datetime(value: str) -> datetime:
    """Parse ISO 8601, accepting a trailing Z."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def is_timezone_aware(value: str | datetime) -> bool:
    try:
        parsed = parse_datetime(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def iso_now(tz=None) -> str:
    """Return a seconds-precision, timezone-aware timestamp."""
    return datetime.now(tz=tz).astimezone().replace(microsecond=0).isoformat()
