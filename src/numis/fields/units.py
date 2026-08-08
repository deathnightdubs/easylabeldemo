"""Parsing physical measurements and money into canonical units.

Canonical units, per docs/design/01 Part 1.5:

===========  ==================
Mass         grams (float)
Length       millimetres (float)
Fineness     parts per thousand, 0-1000 (float)
Angle        degrees (float)
Money        minor units (int)
===========  ==================

The original expression is always kept in ``entered_as`` so that ``22K`` still reads as
``22K`` even though it is stored as 916.667.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from fractions import Fraction

from ..errors import FieldParseError

_NUMBER = r"[+-]?(?:\d+\s+\d+/\d+|\d+/\d+|\d*\.?\d+)"
_MEASURE_RE = re.compile(rf"^({_NUMBER})\s*([a-zA-Z°\"'′″]*)$")

MASS_TO_GRAM = {
    "g": 1.0,
    "gram": 1.0,
    "grams": 1.0,
    "gm": 1.0,
    "mg": 0.001,
    "kg": 1000.0,
    "gr": 0.06479891,  # grain
    "grain": 0.06479891,
    "grains": 0.06479891,
    "ozt": 31.1034768,  # troy ounce
    "oz": 31.1034768,
    "dwt": 1.55517384,  # pennyweight
}

LENGTH_TO_MM = {
    "mm": 1.0,
    "cm": 10.0,
    "m": 1000.0,
    "in": 25.4,
    "inch": 25.4,
    "inches": 25.4,
    '"': 25.4,
    "″": 25.4,
}


def parse_number(text: str) -> float:
    """Parse a decimal, fraction or mixed number such as ``1 1/4``.

    Mirrors the behaviour of the existing label generator, which accepts fractional
    measurements in its config.
    """
    value = text.strip()
    try:
        if " " in value:
            sign = -1.0 if value.startswith("-") else 1.0
            whole, frac = value.lstrip("+-").split()
            return sign * (float(whole) + float(Fraction(frac)))
        if "/" in value:
            return float(Fraction(value))
        return float(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise FieldParseError(text, "number", "not a number") from exc


def _split(raw: object, data_type: str) -> tuple[float, str]:
    text = str(raw).strip()
    if not text:
        raise FieldParseError(raw, data_type, "empty")
    match = _MEASURE_RE.match(text)
    if not match:
        raise FieldParseError(raw, data_type, "expected a number with an optional unit")
    number, unit = match.groups()
    return parse_number(number), unit.strip().lower()


def parse_mass(raw: object, default_unit: str = "g") -> float:
    """Return grams."""
    number, unit = _split(raw, "weight")
    scale = MASS_TO_GRAM.get(unit or default_unit)
    if scale is None:
        raise FieldParseError(raw, "weight", f"unknown mass unit {unit!r}")
    return number * scale


def parse_length(raw: object, default_unit: str = "mm") -> float:
    """Return millimetres."""
    number, unit = _split(raw, "dimension")
    scale = LENGTH_TO_MM.get(unit or default_unit)
    if scale is None:
        raise FieldParseError(raw, "dimension", f"unknown length unit {unit!r}")
    return number * scale


def parse_purity(raw: object) -> float:
    """Return fineness in parts per thousand.

    ``0.900``, ``900``, ``90%`` and ``22K`` are the same value expressed four ways. Where no
    unit is given the magnitude decides, because the alternatives are implausible:

    * below 1        -> a fraction, so ``0.925`` is 925
    * 1 to 100       -> a percentage, since 92.5 per mille would be 9% pure
    * above 100      -> already per mille
    """
    text = str(raw).strip()
    if not text:
        raise FieldParseError(raw, "purity", "empty")

    lowered = text.lower().replace(" ", "")
    if lowered.endswith("%"):
        return parse_number(lowered[:-1]) * 10.0
    for suffix in ("karat", "carat", "kt", "ct", "k"):
        if lowered.endswith(suffix):
            karat = parse_number(lowered[: -len(suffix)])
            if not 0 <= karat <= 24:
                raise FieldParseError(raw, "purity", "karat must be between 0 and 24")
            return karat / 24.0 * 1000.0
    if lowered.endswith("‰"):
        return parse_number(lowered[:-1])

    value = parse_number(lowered)
    if value < 0:
        raise FieldParseError(raw, "purity", "cannot be negative")
    if value < 1:
        return value * 1000.0
    if value <= 100:
        return value * 10.0
    if value > 1000:
        raise FieldParseError(raw, "purity", "cannot exceed 1000 per mille")
    return value


def parse_angle(raw: object) -> float:
    """Return degrees. Accepts ``180``, ``180°`` and clock notation such as ``6h``."""
    text = str(raw).strip().lower().replace("o'clock", "h").replace(" ", "")
    if not text:
        raise FieldParseError(raw, "angle", "empty")
    if text.endswith("h"):
        hours = parse_number(text[:-1])
        return (hours % 12) * 30.0
    if text.endswith("°") or text.endswith("deg"):
        text = text.rstrip("deg°")
    return parse_number(text) % 360.0


def parse_money(raw: object, decimals: int = 2) -> int:
    """Return minor units.

    Uses :class:`~decimal.Decimal` throughout: money arithmetic accumulates across a
    lifetime of transactions and floating point would not survive it.
    """
    text = str(raw).strip()
    if not text:
        raise FieldParseError(raw, "money", "empty")
    cleaned = re.sub(r"[^\d.,\-+]", "", text)
    # 1.234,56 (European) versus 1,234.56 (Anglo): the last separator is the decimal one.
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif cleaned.count(",") == 1 and len(cleaned.split(",")[-1]) in (1, 2):
        cleaned = cleaned.replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")

    try:
        amount = Decimal(cleaned)
    except InvalidOperation as exc:
        raise FieldParseError(raw, "money", "not an amount") from exc
    return int((amount * (10**decimals)).to_integral_value(rounding="ROUND_HALF_UP"))


def format_money(amount_minor: int, decimals: int = 2, symbol: str = "") -> str:
    scaled = Decimal(amount_minor) / (10**decimals)
    return f"{symbol}{scaled:,.{decimals}f}"


def format_mass(grams: float, unit: str = "g", decimals: int = 2) -> str:
    scale = MASS_TO_GRAM.get(unit.lower())
    if scale is None:
        raise ValueError(f"unknown mass unit {unit!r}")
    return f"{grams / scale:.{decimals}f} {unit}"


def format_length(mm: float, unit: str = "mm", decimals: int = 2) -> str:
    scale = LENGTH_TO_MM.get(unit.lower())
    if scale is None:
        raise ValueError(f"unknown length unit {unit!r}")
    return f"{mm / scale:.{decimals}f} {unit}"


def format_purity(per_mille: float, style: str = "decimal") -> str:
    if style == "permille":
        return f"{per_mille:.0f}"
    if style == "percent":
        return f"{per_mille / 10:.1f}%"
    if style == "karat":
        return f"{per_mille / 1000 * 24:.1f}K".replace(".0K", "K")
    return f"{per_mille / 1000:.3f}"


def format_angle(degrees: float, style: str = "degrees") -> str:
    if style == "clock":
        hours = (degrees / 30.0) % 12
        return f"{12 if hours == 0 else hours:g}h"
    return f"{degrees:g}°"
