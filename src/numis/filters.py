"""Describing "show me only these coins", and turning that into SQL.

A filter is a tree: a group holding criteria and further groups, matched with *all* or *any*, and
optionally negated. Nesting is what lets a collector ask the questions they actually have — "cast
bronze, either Qianlong or Jiaqing, not holed" — which no flat list of conditions can express.

Two decisions worth stating.

**Every criterion compiles to EXISTS, not a join.** A coin can hold several values for one field,
several catalogue numbers and several grades. Joining multiplies rows, so ``ruler is not
Victoria`` would match a coin that also has a second ruler value, and counting rows would
double-count. ``EXISTS``/``NOT EXISTS`` asks the question that was meant — *does this coin have
any value like that* — and composes correctly under AND, OR and NOT.

**Operators come from the field registry.** :data:`numis.fields.registry.FieldType.filter_operators`
already declares what each data type can be asked, so a text field offers ``contains`` and a date
field offers ``in_decade`` without this module knowing which is which.

Nothing here imports the service layer, so a filter can be built, described, saved and read back
without a database.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

#: Targets that are properties of the coin itself rather than of a field it holds.
IDENTITY_TARGETS = {
    "__id__": "ID",
    "__name__": "Name",
    "__subcollection__": "Subcollection",
    "__status__": "Status",
    "__favourite__": "Favourite",
}

#: Targets that ask about one of the special systems.
SYSTEM_TARGETS = {
    "catalogues": "Catalogue number",
    "grades": "Grade",
    "certifications": "Certification",
    "links": "Link",
}

#: Operators offered for the identity targets. ``is_any_of`` is what makes "these particular
#: coins" expressible, which is how a hand-picked selection becomes a saved view.
ID_OPS = ("is", "is_not", "is_any_of", "contains", "starts_with", "empty", "not_empty")
NAME_OPS = ("is", "is_not", "contains", "not_contains", "starts_with", "ends_with", "empty",
            "not_empty")
STATUS_OPS = ("is", "is_not", "is_any_of")
BOOL_TARGET_OPS = ("is_true", "is_false")

#: Operators for the special systems. The grade ones compare the calculated value, so a grade
#: from one standard can be measured against a grade from another.
CATALOGUE_OPS = ("in_catalogue", "not_in_catalogue", "number_is", "number_contains",
                 "number_between", "empty", "not_empty")
GRADE_OPS = ("graded_by", "at_least", "at_most", "between", "has_modifier", "is_problem",
             "empty", "not_empty")
CERTIFICATION_OPS = ("certified_by", "not_certified_by", "number_is", "empty", "not_empty")
LINK_OPS = ("of_kind", "empty", "not_empty")

#: How many values each operator expects. Anything absent takes exactly one.
ARITY: dict[str, int] = {
    "empty": 0,
    "not_empty": 0,
    "is_true": 0,
    "is_false": 0,
    "unknown": 0,
    "is_problem": 0,
    "between": 2,
    "between_years": 2,
    "number_between": 2,
    "is_any_of": -1,  # one or more
}

#: How each operator reads in a sentence, for describing a filter back to the user.
OPERATOR_WORDS = {
    "is": "is",
    "is_not": "is not",
    "is_any_of": "is any of",
    "contains": "contains",
    "not_contains": "does not contain",
    "starts_with": "starts with",
    "ends_with": "ends with",
    "empty": "is empty",
    "not_empty": "is filled in",
    "eq": "is",
    "ne": "is not",
    "lt": "is less than",
    "lte": "is at most",
    "gt": "is more than",
    "gte": "is at least",
    "between": "is between",
    "in_year": "is in",
    "between_years": "is between",
    "before": "is before",
    "after": "is after",
    "in_decade": "is in the decade of",
    "in_century": "is in the century of",
    "is_circa": "is approximate",
    "unknown": "is unknown",
    "is_true": "is yes",
    "is_false": "is no",
    "in_catalogue": "is in catalogue",
    "not_in_catalogue": "is not in catalogue",
    "number_is": "number is",
    "number_contains": "number contains",
    "number_between": "number is between",
    "graded_by": "was graded by",
    "at_least": "is at least",
    "at_most": "is at most",
    "has_modifier": "has the modifier",
    "is_problem": "is a problem grade",
    "certified_by": "was certified by",
    "not_certified_by": "was not certified by",
    "of_kind": "is of kind",
}


class FilterError(ValueError):
    """A filter that cannot be carried out, phrased for the person who wrote it."""


def operators_for(target: str, data_type: str | None = None) -> tuple[str, ...]:
    """What can be asked of this target.

    Field targets defer to the registry so a new field type arrives with its operators already
    decided; everything else is fixed here.
    """
    if target == "__id__":
        return ID_OPS
    if target == "__name__":
        return NAME_OPS
    if target in ("__status__", "__subcollection__"):
        return STATUS_OPS if target == "__status__" else NAME_OPS
    if target == "__favourite__":
        return BOOL_TARGET_OPS
    if target == "catalogues":
        return CATALOGUE_OPS
    if target == "grades":
        return GRADE_OPS
    if target == "certifications":
        return CERTIFICATION_OPS
    if target == "links":
        return LINK_OPS
    if target.startswith("field:"):
        from .fields import get_field_type

        if data_type is None:
            raise FilterError(f"{target!r} needs a data type before its operators are known")
        return get_field_type(data_type).filter_operators
    raise FilterError(f"unknown filter target {target!r}")


def expected_values(operator: str) -> int:
    """How many values an operator takes. ``-1`` means one or more."""
    return ARITY.get(operator, 1)


@dataclass(frozen=True)
class Criterion:
    """One question asked of a coin."""

    target: str
    operator: str
    values: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.target:
            raise FilterError("a criterion needs something to test")
        if not self.operator:
            raise FilterError("a criterion needs an operator")

    @property
    def field_key(self) -> str | None:
        """The field this asks about, or ``None`` for an identity or system target."""
        return self.target.split(":", 1)[1] if self.target.startswith("field:") else None

    def validate(self, label: str | None = None) -> None:
        """Check the shape of this criterion, naming the column as the user sees it."""
        wanted = expected_values(self.operator)
        name = label or self.field_key or IDENTITY_TARGETS.get(self.target) or self.target
        given = len([value for value in self.values if str(value).strip()])
        if wanted == -1:
            if given < 1:
                raise FilterError(f"“{name} {OPERATOR_WORDS.get(self.operator, self.operator)}” "
                                  "needs at least one value")
        elif given != wanted:
            word = OPERATOR_WORDS.get(self.operator, self.operator)
            expected = "no value" if wanted == 0 else f"{wanted} value(s)"
            raise FilterError(f"“{name} {word}” takes {expected}, but {given} were given")

    def describe(self, label: str | None = None) -> str:
        name = label or self.field_key or IDENTITY_TARGETS.get(self.target) or SYSTEM_TARGETS.get(
            self.target, self.target
        )
        word = OPERATOR_WORDS.get(self.operator, self.operator)
        if expected_values(self.operator) == 0:
            return f"{name} {word}"
        if self.operator in ("between", "between_years", "number_between") and len(
            self.values
        ) == 2:
            return f"{name} {word} {self.values[0]} and {self.values[1]}"
        if self.operator == "is_any_of":
            return f"{name} {word} {', '.join(self.values)}"
        return f"{name} {word} {self.values[0] if self.values else ''}".strip()

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"target": self.target, "operator": self.operator}
        if self.values:
            data["values"] = list(self.values)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Criterion:
        raw = data.get("values") or ()
        if isinstance(raw, str):
            raw = [raw]
        return cls(
            target=str(data.get("target", "")),
            operator=str(data.get("operator", "")),
            values=tuple(str(value) for value in raw),
        )


@dataclass(frozen=True)
class FilterGroup:
    """Criteria and nested groups, matched together."""

    match: str = "all"
    criteria: tuple[Criterion, ...] = ()
    groups: tuple[FilterGroup, ...] = field(default_factory=tuple)
    negate: bool = False

    def __post_init__(self) -> None:
        if self.match not in ("all", "any"):
            raise FilterError(f"a group matches 'all' or 'any', not {self.match!r}")

    def __bool__(self) -> bool:
        """A group with nothing in it is no filter at all."""
        return bool(self.criteria) or any(bool(group) for group in self.groups)

    def is_empty(self) -> bool:
        return not bool(self)

    def count(self) -> int:
        """How many questions this asks, at any depth."""
        return len(self.criteria) + sum(group.count() for group in self.groups)

    def validate(self, labels: Mapping[str, str] | None = None) -> None:
        labels = labels or {}
        for criterion in self.criteria:
            criterion.validate(labels.get(criterion.target))
        for group in self.groups:
            group.validate(labels)

    def describe(self, labels: Mapping[str, str] | None = None) -> str:
        """The filter as a sentence, for a status bar or a tooltip."""
        labels = labels or {}
        parts = [criterion.describe(labels.get(criterion.target)) for criterion in self.criteria]
        parts += [f"({group.describe(labels)})" for group in self.groups if group]
        if not parts:
            return "no filter"
        joiner = " and " if self.match == "all" else " or "
        sentence = joiner.join(parts)
        return f"not ({sentence})" if self.negate else sentence

    def with_criterion(self, criterion: Criterion) -> FilterGroup:
        return FilterGroup(
            match=self.match,
            criteria=(*self.criteria, criterion),
            groups=self.groups,
            negate=self.negate,
        )

    def without(self, index: int) -> FilterGroup:
        remaining = [c for position, c in enumerate(self.criteria) if position != index]
        return FilterGroup(
            match=self.match,
            criteria=tuple(remaining),
            groups=self.groups,
            negate=self.negate,
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"match": self.match}
        if self.criteria:
            data["criteria"] = [criterion.to_dict() for criterion in self.criteria]
        if self.groups:
            data["groups"] = [group.to_dict() for group in self.groups]
        if self.negate:
            data["negate"] = True
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> FilterGroup:
        """Read a filter back, ignoring what cannot be understood.

        Forgiving on purpose: a saved view written by a later version should open with the parts
        that still make sense rather than refusing to open at all.
        """
        if not data:
            return cls()
        match = data.get("match", "all")
        if match not in ("all", "any"):
            match = "all"
        criteria = []
        for entry in data.get("criteria") or ():
            if not isinstance(entry, Mapping):
                continue
            try:
                criteria.append(Criterion.from_dict(entry))
            except FilterError:
                continue
        groups = []
        for entry in data.get("groups") or ():
            if isinstance(entry, Mapping):
                nested = cls.from_dict(entry)
                if nested:
                    groups.append(nested)
        return cls(
            match=match,
            criteria=tuple(criteria),
            groups=tuple(groups),
            negate=bool(data.get("negate")),
        )

    @classmethod
    def from_json(cls, text: str | None) -> FilterGroup:
        try:
            loaded = json.loads(text or "{}")
        except (TypeError, ValueError):
            return cls()
        return cls.from_dict(loaded if isinstance(loaded, Mapping) else {})

    @classmethod
    def of(cls, *criteria: Criterion, match: str = "all") -> FilterGroup:
        """Convenience for the common flat case."""
        return cls(match=match, criteria=tuple(criteria))


#: No filter: everything passes.
NO_FILTER = FilterGroup()


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SortKey:
    """One level of ordering."""

    target: str
    descending: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"target": self.target, "descending": self.descending}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SortKey:
        return cls(target=str(data.get("target", "")), descending=bool(data.get("descending")))

    def describe(self, label: str | None = None) -> str:
        name = label or self.target.removeprefix("field:")
        name = IDENTITY_TARGETS.get(self.target, name)
        return f"{name} {'descending' if self.descending else 'ascending'}"


def sort_to_json(keys: Sequence[SortKey]) -> str:
    return json.dumps([key.to_dict() for key in keys])


def sort_from_json(text: str | None) -> tuple[SortKey, ...]:
    try:
        loaded = json.loads(text or "[]")
    except (TypeError, ValueError):
        return ()
    if not isinstance(loaded, list):
        return ()
    keys = []
    for entry in loaded:
        if isinstance(entry, Mapping) and entry.get("target"):
            keys.append(SortKey.from_dict(entry))
    return tuple(keys)


def add_sort_key(
    existing: Sequence[SortKey], target: str, *, descending: bool, additional: bool
) -> tuple[SortKey, ...]:
    """Work out the new sort order after a header click.

    An ordinary click sorts by that column alone. Adding a key keeps the ones already chosen and
    puts the new one last, so "by country, then by date" is built by clicking twice — and
    re-adding a column already in the list moves it rather than duplicating it.
    """
    key = SortKey(target=target, descending=descending)
    if not additional:
        return (key,)
    kept = [entry for entry in existing if entry.target != target]
    return (*kept, key)


def describe_sort(keys: Iterable[SortKey], labels: Mapping[str, str] | None = None) -> str:
    labels = labels or {}
    parts = [key.describe(labels.get(key.target)) for key in keys]
    return ", then ".join(parts) if parts else "the order they were added"
