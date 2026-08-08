"""The field type registry.

Each type knows how to parse user input into canonical storage columns, format it back for
display, and which filter operators it offers. Adding a field type is one entry here plus
one editor widget in the (future) UI layer, which never lives in this package.

There is deliberately no ``grade`` type: grading is a special system, because a coin can
carry several grades from several standards with modifiers and history. See
docs/design/02, Part 4.2.

There is also no ``lookup`` type and no ``category`` type. Remembered-entry vocabularies and
fixed lists were both dropped: every field is plain ``text`` for now, and filtering text is good
enough for finding things. Reinstating either is additive and changes no stored data.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..errors import FieldParseError, UnknownFieldType
from . import units
from .dates import parse_coin_date

#: Operator sets, named so filter-builder code and tests share one definition.
TEXT_OPS = ("is", "is_not", "contains", "not_contains", "starts_with", "ends_with", "empty",
            "not_empty")
NUMERIC_OPS = ("eq", "ne", "lt", "lte", "gt", "gte", "between", "empty", "not_empty")
DATE_OPS = ("in_year", "between_years", "before", "after", "in_decade", "in_century",
            "is_circa", "unknown", "empty", "not_empty")
BOOL_OPS = ("is_true", "is_false", "empty")
PRESENCE_OPS = ("empty", "not_empty")

_APPROX_RE = re.compile(r"^\s*(?:~|c\.?|ca\.?|circa|approx\.?)\s*", re.IGNORECASE)


@dataclass(frozen=True)
class FieldType:
    """Behaviour for one ``data_type``."""

    key: str
    label: str
    #: Key into :data:`numis.models.VALUE_MODELS`, or ``None`` for computed fields.
    storage: str | None
    canonical_unit: str | None
    supports_multi: bool
    filter_operators: tuple[str, ...]
    #: ``parse(raw, config) -> dict`` of column values for the storage table.
    parse: Callable[[Any, dict[str, Any]], dict[str, Any]]
    #: ``format(columns, config) -> str`` for display.
    format: Callable[[dict[str, Any], dict[str, Any]], str]
    #: Column used for ORDER BY within the storage table.
    sort_column: str | None = "value"
    description: str = ""
    default_config: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# text
# ---------------------------------------------------------------------------


def _leading_number(text: str) -> float | None:
    """The number a value starts with, if any: ``10 wen`` -> 10."""
    match = re.match(r"^\s*(-?\d+(?:\.\d+)?)", text)
    return float(match.group(1)) if match else None


def _parse_text(raw: Any, config: dict[str, Any]) -> dict[str, Any]:
    value = "" if raw is None else str(raw).strip()
    if not value:
        raise FieldParseError(raw, "text", "empty")
    if config.get("transform") == "upper":
        value = value.upper()
    elif config.get("transform") == "title":
        value = value.title()
    maximum = config.get("max_length")
    if maximum and len(value) > maximum:
        raise FieldParseError(raw, "text", f"longer than {maximum} characters")
    pattern = config.get("pattern")
    if pattern and not re.fullmatch(pattern, value):
        raise FieldParseError(raw, "text", f"does not match {pattern}")

    columns: dict[str, Any] = {
        "value": value,
        "sort_value": None,
        "sort_source": "none",
        "needs_review": 0,
    }

    # Numeric ordering is opt-in per field. Without this, every ordinary text column
    # (country, ruler, notes) would be flagged for review on every entry, which would
    # train the user to ignore the flag.
    if config.get("numeric_sort"):
        proposed = _leading_number(value)
        if proposed is None:
            columns["needs_review"] = 1
        else:
            columns["sort_value"] = proposed
            columns["sort_source"] = "auto"
    return columns


def _format_text(columns: dict[str, Any], config: dict[str, Any]) -> str:
    return str(columns.get("value", ""))


# ---------------------------------------------------------------------------
# numbers and measurements
# ---------------------------------------------------------------------------


def _numeric_parser(
    convert: Callable[[Any, dict[str, Any]], float], data_type: str
) -> Callable[[Any, dict[str, Any]], dict[str, Any]]:
    def parse(raw: Any, config: dict[str, Any]) -> dict[str, Any]:
        text = "" if raw is None else str(raw).strip()
        if not text:
            raise FieldParseError(raw, data_type, "empty")
        approximate = bool(_APPROX_RE.match(text))
        if approximate:
            text = _APPROX_RE.sub("", text).strip()
        value = convert(text, config)

        minimum, maximum = config.get("min"), config.get("max")
        if minimum is not None and value < minimum:
            raise FieldParseError(raw, data_type, f"below the minimum {minimum}")
        if maximum is not None and value > maximum:
            raise FieldParseError(raw, data_type, f"above the maximum {maximum}")

        entered = str(raw).strip()
        return {
            "value": value,
            # Keep the original expression only when it differs from the canonical value,
            # so '22K' survives but a plain '27.15' does not create noise.
            "entered_as": entered if entered != f"{value:g}" else None,
            "is_approximate": int(approximate),
        }

    return parse


def _plain_number(text: str, config: dict[str, Any]) -> float:
    return units.parse_number(text)


def _format_number(columns: dict[str, Any], config: dict[str, Any]) -> str:
    value = columns.get("value")
    if value is None:
        return ""
    decimals = config.get("decimals", 2)
    label = config.get("unit_label")
    text = f"{float(value):.{decimals}f}"
    return f"{text} {label}" if label else text


def _format_weight(columns: dict[str, Any], config: dict[str, Any]) -> str:
    value = columns.get("value")
    if value is None:
        return ""
    return units.format_mass(
        float(value), config.get("display_unit", "g"), config.get("decimals", 2)
    )


def _format_dimension(columns: dict[str, Any], config: dict[str, Any]) -> str:
    value = columns.get("value")
    if value is None:
        return ""
    return units.format_length(
        float(value), config.get("display_unit", "mm"), config.get("decimals", 2)
    )


def _format_purity(columns: dict[str, Any], config: dict[str, Any]) -> str:
    value = columns.get("value")
    if value is None:
        return ""
    return units.format_purity(float(value), config.get("display_style", "decimal"))


def _format_angle(columns: dict[str, Any], config: dict[str, Any]) -> str:
    value = columns.get("value")
    if value is None:
        return ""
    return units.format_angle(float(value), config.get("display_style", "degrees"))


# ---------------------------------------------------------------------------
# money, date, boolean, rating, json
# ---------------------------------------------------------------------------


def _parse_money(raw: Any, config: dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw, dict):
        amount, as_of = raw.get("amount"), raw.get("as_of")
    else:
        amount, as_of = raw, None
    decimals = config.get("decimals", 2)
    columns: dict[str, Any] = {"amount_minor": units.parse_money(amount, decimals)}
    if config.get("dated"):
        columns["as_of"] = date.fromisoformat(as_of) if isinstance(as_of, str) else as_of
    return columns


def _format_money(columns: dict[str, Any], config: dict[str, Any]) -> str:
    amount = columns.get("amount_minor")
    if amount is None:
        return ""
    text = units.format_money(int(amount), config.get("decimals", 2), config.get("symbol", ""))
    as_of = columns.get("as_of")
    return f"{text} ({as_of})" if as_of else text


def _parse_date(raw: Any, config: dict[str, Any]) -> dict[str, Any]:
    parsed = parse_coin_date(raw)
    if not config.get("allow_range", True) and parsed.precision == "range":
        raise FieldParseError(raw, "date", "ranges are not allowed on this field")
    return parsed.as_columns()


def _format_date(columns: dict[str, Any], config: dict[str, Any]) -> str:
    return str(columns.get("display", ""))


_TRUE = {"1", "true", "yes", "y", "on", "t"}
_FALSE = {"0", "false", "no", "n", "off", "f"}


def _parse_bool(raw: Any, config: dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw, bool):
        return {"value": int(raw)}
    text = str(raw).strip().lower()
    if text in _TRUE:
        return {"value": 1}
    if text in _FALSE:
        return {"value": 0}
    raise FieldParseError(raw, "boolean", "expected yes or no")


def _format_bool(columns: dict[str, Any], config: dict[str, Any]) -> str:
    value = columns.get("value")
    if value is None:
        return ""
    return config.get("true_label", "Yes") if value else config.get("false_label", "No")


def _parse_rating(raw: Any, config: dict[str, Any]) -> dict[str, Any]:
    maximum = config.get("max_stars", 5)
    value = units.parse_number(str(raw))
    if not 0 <= value <= maximum:
        raise FieldParseError(raw, "rating", f"must be between 0 and {maximum}")
    if not config.get("allow_half") and value != int(value):
        raise FieldParseError(raw, "rating", "half stars are not enabled on this field")
    return {"value": float(value), "entered_as": None, "is_approximate": 0}


def _format_rating(columns: dict[str, Any], config: dict[str, Any]) -> str:
    value = columns.get("value")
    if value is None:
        return ""
    return f"{float(value):g}/{config.get('max_stars', 5)}"


def _parse_json(raw: Any, config: dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw, str):
        try:
            json.loads(raw)
        except json.JSONDecodeError as exc:
            raise FieldParseError(raw, "json", "not valid JSON") from exc
        return {"value": raw}
    return {"value": json.dumps(raw, ensure_ascii=False, sort_keys=True)}


def _format_json(columns: dict[str, Any], config: dict[str, Any]) -> str:
    return str(columns.get("value", ""))


def _parse_computed(raw: Any, config: dict[str, Any]) -> dict[str, Any]:
    raise FieldParseError(raw, "computed", "computed fields are derived, not entered")


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

REGISTRY: dict[str, FieldType] = {}


def register(field_type: FieldType) -> FieldType:
    REGISTRY[field_type.key] = field_type
    return field_type


register(
    FieldType(
        key="text",
        label="Text",
        storage="text",
        canonical_unit=None,
        supports_multi=True,
        filter_operators=TEXT_OPS,
        parse=_parse_text,
        format=_format_text,
        description="Short free text. Set numeric_sort to order it by a leading number.",
        default_config={"numeric_sort": False},
    )
)

register(
    FieldType(
        key="long_text",
        label="Long text",
        storage="text",
        canonical_unit=None,
        supports_multi=False,
        filter_operators=("contains", "not_contains", *PRESENCE_OPS),
        parse=_parse_text,
        format=_format_text,
        sort_column=None,
        description="Notes and descriptions. Included in full-text search.",
        default_config={"rows": 4},
    )
)

register(
    FieldType(
        key="number",
        label="Number",
        storage="number",
        canonical_unit=None,
        supports_multi=True,
        filter_operators=NUMERIC_OPS,
        parse=_numeric_parser(_plain_number, "number"),
        format=_format_number,
        default_config={"decimals": 2},
    )
)

register(
    FieldType(
        key="weight",
        label="Weight",
        storage="number",
        canonical_unit="g",
        supports_multi=False,
        filter_operators=NUMERIC_OPS,
        parse=_numeric_parser(
            lambda text, config: units.parse_mass(text, config.get("input_unit", "g")), "weight"
        ),
        format=_format_weight,
        default_config={"display_unit": "g", "decimals": 2},
    )
)

register(
    FieldType(
        key="dimension",
        label="Dimension",
        storage="number",
        canonical_unit="mm",
        supports_multi=False,
        filter_operators=NUMERIC_OPS,
        parse=_numeric_parser(
            lambda text, config: units.parse_length(text, config.get("input_unit", "mm")),
            "dimension",
        ),
        format=_format_dimension,
        default_config={"display_unit": "mm", "decimals": 2},
    )
)

register(
    FieldType(
        key="purity",
        label="Fineness",
        storage="number",
        canonical_unit="per mille",
        supports_multi=False,
        filter_operators=NUMERIC_OPS,
        parse=_numeric_parser(lambda text, config: units.parse_purity(text), "purity"),
        format=_format_purity,
        default_config={"display_style": "decimal"},
    )
)

register(
    FieldType(
        key="angle",
        label="Angle",
        storage="number",
        canonical_unit="degrees",
        supports_multi=False,
        filter_operators=NUMERIC_OPS,
        parse=_numeric_parser(lambda text, config: units.parse_angle(text), "angle"),
        format=_format_angle,
        default_config={"display_style": "degrees"},
    )
)

register(
    FieldType(
        key="money",
        label="Money",
        storage="money",
        canonical_unit="minor units",
        supports_multi=False,
        filter_operators=NUMERIC_OPS,
        parse=_parse_money,
        format=_format_money,
        sort_column="amount_minor",
        default_config={"decimals": 2, "dated": False},
    )
)

register(
    FieldType(
        key="date",
        label="Date",
        storage="date",
        canonical_unit=None,
        supports_multi=True,
        filter_operators=DATE_OPS,
        parse=_parse_date,
        format=_format_date,
        sort_column="sort_value",
        description="A coin date: a year, a range, an approximation or another era.",
        default_config={"allow_range": True, "allow_circa": True},
    )
)

register(
    FieldType(
        key="boolean",
        label="Yes or no",
        storage="bool",
        canonical_unit=None,
        supports_multi=False,
        filter_operators=BOOL_OPS,
        parse=_parse_bool,
        format=_format_bool,
        default_config={"true_label": "Yes", "false_label": "No"},
    )
)

register(
    FieldType(
        key="rating",
        label="Rating",
        storage="number",
        canonical_unit=None,
        supports_multi=False,
        filter_operators=NUMERIC_OPS,
        parse=_parse_rating,
        format=_format_rating,
        default_config={"max_stars": 5, "allow_half": False},
    )
)

register(
    FieldType(
        key="json",
        label="Structured data",
        storage="json",
        canonical_unit=None,
        supports_multi=False,
        filter_operators=PRESENCE_OPS,
        parse=_parse_json,
        format=_format_json,
        sort_column=None,
        description="Display-only escape hatch; never sorted or filtered.",
    )
)

register(
    FieldType(
        key="computed",
        label="Calculated",
        storage=None,
        canonical_unit=None,
        supports_multi=False,
        filter_operators=NUMERIC_OPS,
        parse=_parse_computed,
        format=_format_number,
        sort_column=None,
        description="Derived from other fields by a formula.",
        default_config={"expression": "", "result_type": "number", "decimals": 2},
    )
)


#: Types whose values carry an editable numeric sort key.
SORT_KEY_TYPES = ("text", "date")

FIELD_TYPE_KEYS = tuple(REGISTRY)


def get_field_type(key: str) -> FieldType:
    try:
        return REGISTRY[key]
    except KeyError:
        raise UnknownFieldType(
            f"unknown field type {key!r}; available: {', '.join(sorted(REGISTRY))}"
        ) from None


def parse_value(data_type: str, raw: Any, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Parse ``raw`` for ``data_type``, merging the type's default config."""
    field_type = get_field_type(data_type)
    merged = {**field_type.default_config, **(config or {})}
    return field_type.parse(raw, merged)


def format_value(
    data_type: str, columns: dict[str, Any], config: dict[str, Any] | None = None
) -> str:
    field_type = get_field_type(data_type)
    merged = {**field_type.default_config, **(config or {})}
    return field_type.format(columns, merged)
