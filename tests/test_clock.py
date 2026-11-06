"""Clock helpers must be boring and timezone-safe."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from scopeguard.clock import as_date, iso, parse_moment, utcnow


def test_utcnow_is_aware_and_truncated() -> None:
    value = utcnow()
    assert value.tzinfo is UTC
    assert value.microsecond == 0


def test_as_date_accepts_date_datetime_and_string() -> None:
    expected = date(2026, 11, 4)
    assert as_date(expected) == expected
    assert as_date(datetime(2026, 11, 4, 23, 59, tzinfo=UTC)) == expected
    assert as_date("2026-11-04") == expected
    assert as_date("2026-11-04T09:00:00Z") == expected


def test_iso_formats_datetime_as_utc_z() -> None:
    moment = datetime(2026, 11, 4, 9, 0, 0, 123456, tzinfo=UTC)
    assert iso(moment) == "2026-11-04T09:00:00Z"


def test_iso_keeps_dates() -> None:
    assert iso(date(2026, 11, 4)) == "2026-11-04"


def test_parse_moment_accepts_z_and_naive() -> None:
    expected = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
    assert parse_moment("2026-11-04T09:00:00Z") == expected
    assert parse_moment("2026-11-04T09:00:00") == expected
    assert parse_moment(" 2026-11-04T10:00:00+01:00 ") == expected


def test_parse_moment_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        parse_moment("not a date")
