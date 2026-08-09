"""How a special-system column displays itself.

A coin can carry any number of catalogue numbers, grades, certifications and links, but a column
is one cell wide. This module holds the user's answer to "which of them, and how much of each",
as a value object that is stored in the column's ``config_json`` and read back when the grid
renders a cell.

Three ways to choose *which*, which is what a collector actually asks for:

``all``
    Every entry, joined together. Honest, and occasionally very wide.
``only``
    Just the ones from one place — only Numista, only Hartill, only PCGS. This is how a column
    becomes a column *about something*: a "Numista" column rather than a "catalogue numbers"
    column, without needing a separate field for every catalogue the collector uses.
``rank``
    The entry the user put first, second, third… Precedence is set by hand in the detail panel,
    because which of two catalogues is the one you cite is a judgement, not something to infer
    from recency or authority.

Then *how much* of each, which differs by system and is why the flags below are not uniform:
a grade has modifiers and a scale, a catalogue number has a catalogue it belongs to, and a link
is either a count or a list.

Nothing here touches the database or Qt, so the rules can be tested on their own.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields, replace
from typing import Any

from . import grading

#: The ways a column can choose which entries to show.
MODES = ("all", "only", "rank")

#: How far precedence goes. Ten is past the point of usefulness, which is the intent.
MAX_RANK = 10

#: What ``only`` is matched against, per system, for the settings dialog to explain itself.
ONLY_MEANING = {
    "catalogues": "catalogue",
    "grades": "source or grader",
    "certifications": "company",
    "links": "kind",
}


@dataclass(frozen=True)
class ColumnDisplay:
    """The display settings for one special-system column.

    One flat object rather than a class per system: a column has exactly one kind, so the
    irrelevant flags are simply unused, and a flat shape survives being written to JSON and read
    back by a later version without a migration.
    """

    # -- which entries ----------------------------------------------------
    mode: str = "all"
    #: For ``only``: the catalogue code, grading company code, grade source or link kind.
    only: str | None = None
    #: For ``rank``: 1 is the entry the user put first.
    rank: int = 1
    separator: str = " · "

    # -- how much of a grade ----------------------------------------------
    show_modifiers: bool = True
    #: ``CAC Gold`` rather than ``CAC``; ``Details — Harshly Cleaned`` rather than ``Details``.
    modifier_details: bool = False
    show_scale: bool = False
    show_source: bool = False
    show_assigned_by: bool = False

    # -- how much of a catalogue number -----------------------------------
    #: ``H 1.01`` rather than ``1.01``. Worth turning off in a single-catalogue column, where
    #: the code is the same on every row and only costs width.
    show_catalogue: bool = True

    # -- how much of a link -----------------------------------------------
    #: Labels instead of a count, for collections small enough that the links are worth reading.
    show_labels: bool = False

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"unknown column mode {self.mode!r}; expected one of {MODES}")

    @property
    def grade_display(self) -> grading.GradeDisplay:
        """The same choices, in the form :mod:`numis.grading` renders from."""
        return grading.GradeDisplay(
            modifiers=self.show_modifiers,
            modifier_details=self.modifier_details,
            scale=self.show_scale,
            source=self.show_source,
            assigned_by=self.show_assigned_by,
        )

    def describe(self, kind: str) -> str:
        """A short sentence for the column's tooltip, so the header explains its own contents."""
        if self.mode == "only" and self.only:
            which = f"only {ONLY_MEANING.get(kind, 'entries')} {self.only}"
        elif self.mode == "rank":
            which = f"the entry ranked {self.rank}"
        else:
            which = "all entries"
        return which[0].upper() + which[1:]

    # -- storage ----------------------------------------------------------

    def to_config(self) -> dict[str, Any]:
        """Only what differs from the defaults, so stored settings stay readable."""
        default = ColumnDisplay()
        return {
            name: getattr(self, name)
            for name in (f.name for f in fields(self))
            if getattr(self, name) != getattr(default, name)
        }

    @classmethod
    def from_config(cls, config: Mapping[str, Any] | None) -> ColumnDisplay:
        """Read settings back, ignoring anything unrecognised or out of range.

        Deliberately forgiving: a column whose settings cannot be understood should render with
        the defaults, not stop the grid from drawing.
        """
        if not config:
            return cls()
        known = {f.name for f in fields(cls)}
        values = {key: value for key, value in config.items() if key in known}

        mode = values.get("mode", "all")
        if mode not in MODES:
            mode = "all"
        values["mode"] = mode

        for flag in (
            "show_modifiers",
            "modifier_details",
            "show_scale",
            "show_source",
            "show_assigned_by",
            "show_catalogue",
            "show_labels",
        ):
            if flag in values:
                values[flag] = bool(values[flag])

        try:
            values["rank"] = max(1, min(MAX_RANK, int(values.get("rank", 1))))
        except (TypeError, ValueError):
            values["rank"] = 1

        only = values.get("only")
        values["only"] = str(only) if only else None
        if not isinstance(values.get("separator", " · "), str):
            values["separator"] = " · "
        return cls(**values)

    @classmethod
    def from_json(cls, text: str | None) -> ColumnDisplay:
        try:
            loaded = json.loads(text or "{}")
        except (TypeError, ValueError):
            return cls()
        return cls.from_config(loaded if isinstance(loaded, Mapping) else {})

    def to_json(self) -> str:
        return json.dumps(self.to_config())

    def with_values(self, **changes: Any) -> ColumnDisplay:
        return replace(self, **changes)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


#: The settings a column falls back to, and what the master view uses when subcollections
#: disagree about how a shared column should look.
DEFAULT_DISPLAY = ColumnDisplay()


def pick(entries: list[Any], display: ColumnDisplay) -> list[Any]:
    """Narrow already-ordered entries down to the ones this column shows.

    ``entries`` must arrive in the user's order of precedence. ``rank`` counts positions in that
    order rather than matching the stored number, because ranks are not uniquely enforced — the
    third entry down is what the user pointed at, whatever integers happen to be on the rows.
    """
    if display.mode == "rank":
        index = display.rank - 1
        return [entries[index]] if 0 <= index < len(entries) else []
    return entries
