"""Custom SQLAlchemy column types.

Timestamps and coin-related dates are stored as ISO-8601 text rather than numbers, so a
collection database stays readable in any SQLite browser. See docs/design/01, Part 1.3.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import String
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


class UtcIso(TypeDecorator):
    """A timezone-aware UTC datetime stored as ``2026-07-26T14:03:11.482Z``.

    Naive datetimes are refused rather than silently assumed to be UTC: guessing a
    timezone is exactly the kind of quiet corruption that is impossible to detect later.
    """

    impl = String
    cache_ok = True

    def process_bind_param(self, value: datetime | str | None, dialect: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if value.tzinfo is None:
            raise ValueError(
                "naive datetime rejected; use numis.sqltypes.utcnow() or attach a timezone"
            )
        moment = value.astimezone(UTC)
        return f"{moment:%Y-%m-%dT%H:%M:%S}.{moment.microsecond // 1000:03d}Z"

    def process_result_value(self, value: str | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        return datetime.fromisoformat(value.replace("Z", "+00:00"))


class DateIso(TypeDecorator):
    """A calendar date stored as ``2026-07-26``.

    Used for real-world event dates such as when a coin was bought. Dates that *describe*
    a coin are a different problem entirely and live in ``field_value_date``.
    """

    impl = String
    cache_ok = True

    def process_bind_param(self, value: date | str | None, dialect: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        return value.isoformat()

    def process_result_value(self, value: str | None, dialect: object) -> date | None:
        if value is None:
            return None
        return date.fromisoformat(value)
