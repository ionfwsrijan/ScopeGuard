"""Clock and deterministic-time helpers.

Every command accepts an explicit ``--as-of`` where it matters. The rest of
the code receives a callable, which keeps tests and demo transcripts stable.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime

Clock = Callable[[], datetime]


def utcnow() -> datetime:
    """Timezone-aware UTC now, truncated to whole seconds."""
    return datetime.now(UTC).replace(microsecond=0)


def as_date(value: date | datetime | str) -> date:
    """Coerce a fixture value to a date without timezone surprises."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def iso(value: date | datetime) -> str:
    if isinstance(value, datetime):
        return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return value.isoformat()


def parse_moment(value: str) -> datetime:
    """Parse an ISO-8601 timestamp, accepting a trailing ``Z``."""
    cleaned = value.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    parsed = datetime.fromisoformat(cleaned)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).replace(microsecond=0)
