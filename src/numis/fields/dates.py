"""Parsing dates that describe coins.

A coin's date is not a calendar date. It may be a reign, an approximation, a range, a
regnal or Hijri expression, or genuinely unknown. This module turns whatever the user typed
into three things at once (docs/design/01, Part 1.6):

1. ``display`` — exactly what they typed, never overwritten
2. ``year_start`` / ``year_end`` — a normalised span for range filtering
3. ``sort_value`` — a numeric ordering key, with provenance recorded

The rule that governs all of it: **the app proposes, the user disposes, and the app always
says which happened.** Anything derived from a span or converted from another calendar is
flagged with ``needs_review`` so the interface can invite confirmation rather than assuming.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

#: Approximate Hijri to Gregorian conversion. Exact conversion needs the month and day,
#: which a coin's date rarely gives, so the result is always flagged for review.
_AH_EPOCH = 622.0
_AH_YEAR_RATIO = 0.970224

_UNDATED = {"", "undated", "unknown", "no date", "n.d.", "nd", "n/a", "-", "?"}

_CIRCA_RE = re.compile(r"^(?:c\.?|ca\.?|circa|about|approx\.?|~)\s*", re.IGNORECASE)
_BC_TOKENS = {"BC", "BCE", "B.C.", "B.C.E."}
_AD_TOKENS = {"AD", "CE", "A.D.", "C.E."}
_ERA = r"(BC|BCE|B\.C\.|B\.C\.E\.|AD|CE|A\.D\.|C\.E\.)"

_ISO_DAY_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_ISO_MONTH_RE = re.compile(r"^(\d{4})-(0?[1-9]|1[0-2])$")
_AH_RE = re.compile(r"^(?:AH\s*(\d{1,4})|(\d{1,4})\s*AH)$", re.IGNORECASE)
_DECADE_RE = re.compile(rf"^(\d{{1,4}}0)s\s*{_ERA}?$", re.IGNORECASE)
_CENTURY_RE = re.compile(
    rf"^(\d{{1,2}})(?:st|nd|rd|th)?\s*(?:c\.|century|cent\.?)\s*{_ERA}?$", re.IGNORECASE
)
_RANGE_RE = re.compile(
    rf"^(\d{{1,4}})\s*{_ERA}?\s*(?:-|to)\s*(\d{{1,4}})\s*{_ERA}?$", re.IGNORECASE
)
_YEAR_RE = re.compile(rf"^(-?\d{{1,4}})\s*{_ERA}?$", re.IGNORECASE)


@dataclass
class ParsedDate:
    """The parsed form of a coin date, matching the ``field_value_date`` columns."""

    display: str
    year_start: int | None = None
    year_end: int | None = None
    month_start: int | None = None
    day_start: int | None = None
    month_end: int | None = None
    day_end: int | None = None
    precision: str = "unknown"
    calendar: str = "gregorian"
    era_label: str | None = None
    sort_value: float | None = None
    sort_source: str = "none"
    needs_review: bool = False
    #: Human-readable explanation of what was inferred, for the interface to show.
    note: str | None = field(default=None, compare=False)

    def as_columns(self) -> dict[str, object]:
        """Column values for ``field_value_date``, excluding the advisory note."""
        data = asdict(self)
        data.pop("note")
        data["needs_review"] = int(data["needs_review"])
        return data


def _is_bc(era: str | None) -> bool:
    return bool(era) and era.upper().replace(" ", "") in {t.upper() for t in _BC_TOKENS}


def _signed(year: int, era: str | None) -> int:
    """Apply an era to a year. BC becomes negative, using the historical convention that
    there is no year zero, so 1 BC is -1."""
    return -year if _is_bc(era) else year


def _finish(parsed: ParsedDate) -> ParsedDate:
    """Derive the sort value and decide whether the user should confirm it."""
    if parsed.year_start is None or parsed.year_end is None:
        parsed.sort_value = None
        parsed.sort_source = "none"
        return parsed

    parsed.sort_value = (parsed.year_start + parsed.year_end) / 2.0
    parsed.sort_source = "auto"

    span = parsed.year_end - parsed.year_start
    if span > 0:
        parsed.needs_review = True
        if parsed.note is None:
            parsed.note = (
                f"Range recognised ({parsed.year_start} to {parsed.year_end}); "
                f"sorting at the midpoint {parsed.sort_value:g}."
            )
    if parsed.calendar != "gregorian":
        parsed.needs_review = True
    return parsed


def parse_coin_date(raw: object) -> ParsedDate:
    """Parse a coin date. Never raises: unreadable input is stored as typed and flagged.

    Refusing to save a date the parser cannot read would be the software claiming to know
    a collection better than its owner, so anything unrecognised is kept verbatim, sorts
    nowhere, and asks for a number.
    """
    display = "" if raw is None else str(raw).strip()
    text = re.sub(r"\s+", " ", display)
    text = text.replace("\u2013", "-").replace("\u2014", "-").replace("\u2212", "-")

    if text.lower() in _UNDATED:
        return ParsedDate(display=display, precision="unknown", note=None)

    circa = bool(_CIRCA_RE.match(text))
    if circa:
        text = _CIRCA_RE.sub("", text).strip()

    # Hijri
    match = _AH_RE.match(text)
    if match:
        ah = int(match.group(1) or match.group(2))
        gregorian = round(_AH_EPOCH + ah * _AH_YEAR_RATIO, 1)
        year = int(gregorian)
        parsed = _finish(
            ParsedDate(
                display=display,
                year_start=year,
                year_end=year,
                precision="circa" if circa else "exact_year",
                calendar="islamic_ah",
                era_label=f"AH {ah}",
                note=(
                    f"Hijri year {ah} recognised; approximately {gregorian:g} CE. "
                    "Confirm or set your own sort year."
                ),
            )
        )
        # The span columns are integers, but sort_value is a float, so keep the fractional
        # conversion: a Hijri year straddles two Gregorian ones and the fraction says which.
        parsed.sort_value = gregorian
        return parsed

    # Full ISO date
    match = _ISO_DAY_RE.match(text)
    if match:
        year, month, day = (int(g) for g in match.groups())
        if 1 <= month <= 12 and 1 <= day <= 31:
            return _finish(
                ParsedDate(
                    display=display,
                    year_start=year,
                    year_end=year,
                    month_start=month,
                    day_start=day,
                    month_end=month,
                    day_end=day,
                    precision="circa" if circa else "exact_day",
                )
            )

    # Year and month
    match = _ISO_MONTH_RE.match(text)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        return _finish(
            ParsedDate(
                display=display,
                year_start=year,
                year_end=year,
                month_start=month,
                month_end=month,
                precision="circa" if circa else "exact_month",
            )
        )

    # Decade
    match = _DECADE_RE.match(text)
    if match:
        start, era = int(match.group(1)), match.group(2)
        if _is_bc(era):
            return _finish(
                ParsedDate(
                    display=display,
                    year_start=-(start + 9),
                    year_end=-start,
                    precision="decade",
                    era_label="BC",
                )
            )
        return _finish(
            ParsedDate(
                display=display, year_start=start, year_end=start + 9, precision="decade"
            )
        )

    # Century
    match = _CENTURY_RE.match(text)
    if match:
        number, era = int(match.group(1)), match.group(2)
        if _is_bc(era):
            start, end = -(number * 100), -(number * 100 - 99)
            return _finish(
                ParsedDate(
                    display=display,
                    year_start=start,
                    year_end=end,
                    precision="century",
                    era_label="BC",
                )
            )
        return _finish(
            ParsedDate(
                display=display,
                year_start=(number - 1) * 100 + 1,
                year_end=number * 100,
                precision="century",
            )
        )

    # Range
    match = _RANGE_RE.match(text)
    if match:
        first, first_era, second, second_era = match.groups()
        # A trailing era applies to both halves: '350-320 BC' means both are BC.
        era_a = first_era or second_era
        era_b = second_era or first_era
        start, end = _signed(int(first), era_a), _signed(int(second), era_b)
        if start > end:
            start, end = end, start
        return _finish(
            ParsedDate(
                display=display,
                year_start=start,
                year_end=end,
                precision="range",
                era_label="BC" if _is_bc(era_a) else None,
            )
        )

    # Single year
    match = _YEAR_RE.match(text)
    if match:
        raw_year, era = match.group(1), match.group(2)
        year = _signed(abs(int(raw_year)), era) if era else int(raw_year)
        return _finish(
            ParsedDate(
                display=display,
                year_start=year,
                year_end=year,
                precision="circa" if circa else "exact_year",
                era_label="BC" if _is_bc(era) else None,
            )
        )

    # Unreadable: keep it, sort nowhere, ask for a number.
    return ParsedDate(
        display=display,
        precision="unknown",
        needs_review=True,
        note="Not recognised as a date. Set the number it should sort at.",
    )


def set_manual_sort_value(parsed: ParsedDate, sort_value: float) -> ParsedDate:
    """Record a sort value the user chose.

    Once set by hand it is never overwritten by the parser, which is what makes the
    proposal mechanism trustworthy.
    """
    parsed.sort_value = float(sort_value)
    parsed.sort_source = "manual"
    parsed.needs_review = False
    parsed.note = None
    return parsed
