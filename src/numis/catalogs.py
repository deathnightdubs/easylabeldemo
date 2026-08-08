"""Catalogue number parsing, ordering and ranges.

Catalogue numbers look numeric and are not. ``2`` must precede ``10``; ``1042a`` must follow
``1042``; and ``A54`` is not an entry in the fifty-thousands, it is a variant *of* 54 and
belongs beside it. Plain text ordering gets all three wrong.

Parsing extracts three parts — an optional letter prefix, the base number, and any remaining
segments — and builds a fixed-width text key that sorts correctly with a plain ``ORDER BY``
and supports range queries with ``BETWEEN``. See docs/design/02, Part 4.1.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

#: Width each numeric segment is padded to. Eight digits covers every real catalogue.
_NUM_WIDTH = 8
#: Width each text segment is padded to.
_TXT_WIDTH = 4
_SEPARATOR = "|"
#: Sorts after any lowercase letter, so an absent prefix can be placed last when a
#: catalogue orders 'A54' before '54'.
_HIGH_SENTINEL = "~" * _TXT_WIDTH
_LOW_SENTINEL = " " * _TXT_WIDTH

_TOKEN_RE = re.compile(r"\d+|[A-Za-z]+")


@dataclass(frozen=True)
class ParsedCatalogNumber:
    """A catalogue number broken into orderable parts."""

    raw: str
    normalised: str
    prefix: str
    base: int
    segments: tuple[str, ...]

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


def strip_code(raw: object, catalog_code: str | None) -> str:
    """Remove a leading catalogue code, keeping the rest exactly as typed.

    The catalogue is identified by a foreign key, so carrying ``KM`` inside the number too
    would both duplicate it on screen (``KM KM#2073``) and make ``KM#2073`` and ``2073``
    look like different numbers to the duplicate check.
    """
    text = str(raw).strip()
    if not catalog_code:
        return text
    pattern = rf"^{re.escape(catalog_code)}\s*#?\s*(?=[\dA-Za-z])"
    return re.sub(pattern, "", text, flags=re.IGNORECASE).strip()


def normalise(raw: object, catalog_code: str | None = None) -> str:
    """Uppercase, strip the catalogue code, ``#`` and whitespace.

    Used for matching and duplicate detection, so ``KM#2.1``, ``km 2.1`` and ``2.1`` in
    catalogue KM all resolve to one key.
    """
    return re.sub(r"[#\s]+", "", strip_code(raw, catalog_code)).upper()


def parse_number(raw: object, *, catalog_code: str | None = None) -> ParsedCatalogNumber:
    """Split a catalogue number into prefix, base number and trailing segments.

    A leading catalogue code is stripped when it matches ``catalog_code``, since the
    catalogue is identified by a foreign key rather than by text in the number. Without
    that, ``KM#2`` and ``2`` in the same catalogue would sort apart.
    """
    text = str(raw).strip()
    if not text:
        raise ValueError("catalogue number cannot be empty")

    cleaned = re.sub(r"[#\s]+", "", text)
    if catalog_code:
        pattern = rf"^{re.escape(catalog_code)}(?=[\dA-Za-z])"
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)

    tokens = _TOKEN_RE.findall(cleaned)
    prefix = ""
    base: int | None = None
    segments: list[str] = []

    for token in tokens:
        if token.isdigit():
            if base is None:
                base = int(token)
            else:
                segments.append(f"{int(token):0{_NUM_WIDTH}d}")
        elif base is None:
            prefix += token.lower()
        else:
            segments.append(token.lower().ljust(_TXT_WIDTH)[:_TXT_WIDTH])

    return ParsedCatalogNumber(
        raw=strip_code(text, catalog_code),
        normalised=normalise(text, catalog_code),
        prefix=prefix.lower(),
        base=base if base is not None else 0,
        segments=tuple(segments),
    )


def sort_key(
    raw: object,
    *,
    catalog_code: str | None = None,
    letter_prefix_order: str = "after",
) -> str:
    """Build the ordering key stored in ``catalog_reference.sort_segments``.

    The base number comes first, which is what keeps letter-prefixed variants next to their
    base entry. ``letter_prefix_order`` chooses whether ``A54`` follows ``54`` (default) or
    precedes it, because catalogues are not consistent with one another.
    """
    parsed = parse_number(raw, catalog_code=catalog_code)
    if parsed.prefix:
        prefix_key = parsed.prefix.ljust(_TXT_WIDTH)[:_TXT_WIDTH]
    else:
        prefix_key = _HIGH_SENTINEL if letter_prefix_order == "before" else _LOW_SENTINEL
    return _SEPARATOR.join([f"{parsed.base:0{_NUM_WIDTH}d}", prefix_key, *parsed.segments])


def range_bounds(
    low: object,
    high: object,
    *,
    catalog_code: str | None = None,
    letter_prefix_order: str = "after",
) -> tuple[str, str]:
    """Inclusive ``BETWEEN`` bounds for a catalogue number range.

    The upper bound is extended so that everything *below* the endpoint in the hierarchy is
    included: a range ending at ``54`` must contain ``54.2``, which sorts after ``54``.
    """
    start = sort_key(low, catalog_code=catalog_code, letter_prefix_order=letter_prefix_order)
    end = sort_key(high, catalog_code=catalog_code, letter_prefix_order=letter_prefix_order)
    return start, end + _SEPARATOR + "\uffff"


def build_reference_columns(
    raw: object,
    *,
    catalog_code: str | None = None,
    letter_prefix_order: str = "after",
) -> dict[str, object]:
    """Column values for a ``catalog_reference`` row."""
    parsed = parse_number(raw, catalog_code=catalog_code)
    return {
        # The code is stripped: it lives in catalog_id, and keeping it here would display as
        # 'KM KM#2073' and defeat duplicate detection against a bare '2073'.
        "number_raw": parsed.raw,
        "number_norm": parsed.normalised,
        "sort_segments": sort_key(
            raw, catalog_code=catalog_code, letter_prefix_order=letter_prefix_order
        ),
        "segments_json": parsed.to_json(),
    }
