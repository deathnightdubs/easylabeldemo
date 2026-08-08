"""Exceptions raised by the core library.

The distinction that matters here is between *refusals* and *warnings*. Per the design
principle "warn, do not block", the library refuses only operations that would corrupt
meaning or lose data silently. Everything else — an implausible weight, a duplicate
certification number — is reported as a :class:`Warning` record and never raised.
"""

from __future__ import annotations

from dataclasses import dataclass


class NumisError(Exception):
    """Base class for every error raised by this library."""


class LibraryError(NumisError):
    """Opening, creating or migrating a library failed."""


class SchemaVersionError(LibraryError):
    """The library was written by a different version of the application."""


class FieldParseError(NumisError):
    """A value could not be parsed into the canonical form for its field type."""

    def __init__(self, raw: object, data_type: str, reason: str) -> None:
        self.raw = raw
        self.data_type = data_type
        self.reason = reason
        super().__init__(f"cannot read {raw!r} as {data_type}: {reason}")


class UnknownFieldType(NumisError):
    """No field type is registered under the given key."""


class BindingNotSet(NumisError):
    """A feature needs a field but the user has not chosen one.

    Carries enough information for the interface to offer to fix it, rather than
    presenting a failure. See docs/design/02, Part 2.
    """

    def __init__(self, feature: str, purpose: str, message: str | None = None) -> None:
        self.feature = feature
        self.purpose = purpose
        super().__init__(
            message
            or f"no field is set as '{purpose}' for {feature}; choose a field or set a constant"
        )


class ConversionError(NumisError):
    """A field type change cannot be performed."""


@dataclass(frozen=True)
class Warning_:  # noqa: N801 - trailing underscore avoids shadowing the builtin
    """A non-blocking concern about user data.

    Collected and surfaced in the interface or a Library Health report. Never raised.
    """

    code: str
    message: str
    specimen_id: int | None = None
    field_key: str | None = None
