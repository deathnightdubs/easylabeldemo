"""Turning a filter tree and a sort order into SQL.

Kept apart from :mod:`numis.filters` so that describing, saving and reading a filter needs no
database, and apart from :mod:`numis.services` so that the translation can be read on its own.

Every criterion becomes an ``EXISTS`` subquery against the coin, never a join. See the module
docstring of :mod:`numis.filters` for why. Sort keys become scalar subqueries for the same
reason: a coin with three catalogue numbers must still occupy exactly one row.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import ColumnElement, Float, and_, case, cast, func, literal, not_, or_, select
from sqlalchemy.orm import Session

from . import constants as C
from .fields import get_field_type, parse_value
from .filters import Criterion, FilterError, FilterGroup, SortKey
from .models import (
    VALUE_MODELS,
    Catalog,
    CatalogReference,
    Certification,
    ExternalLink,
    FieldDefinition,
    FieldValueDate,
    GradeModifier,
    GradingCompany,
    Specimen,
    SpecimenGrade,
    SpecimenGradeModifier,
    Subcollection,
)


def _first(values: Sequence[str]) -> str:
    return values[0].strip() if values else ""


def _number(criterion: Criterion, position: int = 0) -> float:
    raw = criterion.values[position] if len(criterion.values) > position else ""
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise FilterError(f"“{raw}” is not a number") from exc


def _year(criterion: Criterion, position: int = 0) -> int:
    raw = criterion.values[position] if len(criterion.values) > position else ""
    text = str(raw).strip()
    negative = text.upper().endswith("BC")
    digits = "".join(char for char in text if char.isdigit())
    if not digits:
        raise FilterError(f"“{raw}” is not a year")
    year = int(digits)
    return -year if negative else year


def _like(column: ColumnElement[Any], pattern: str) -> ColumnElement[bool]:
    """Case-insensitive matching, which is what a person means by "contains"."""
    return func.lower(column).like(pattern.lower())


# ---------------------------------------------------------------------------
# Text-shaped comparisons, shared by the identity targets and text fields
# ---------------------------------------------------------------------------


def _text_clause(
    column: ColumnElement[Any], criterion: Criterion
) -> ColumnElement[bool] | None:
    """A comparison against a text column, or ``None`` if the operator is about presence."""
    operator, value = criterion.operator, _first(criterion.values)
    if operator in ("is", "eq"):
        return func.lower(column) == value.lower()
    if operator in ("is_not", "ne"):
        return func.lower(column) != value.lower()
    if operator == "contains":
        return _like(column, f"%{value}%")
    if operator == "not_contains":
        return not_(_like(column, f"%{value}%"))
    if operator == "starts_with":
        return _like(column, f"{value}%")
    if operator == "ends_with":
        return _like(column, f"%{value}")
    if operator == "is_any_of":
        wanted = [entry.strip().lower() for entry in criterion.values if entry.strip()]
        return func.lower(column).in_(wanted)
    return None


def _numeric_clause(
    column: ColumnElement[Any], criterion: Criterion, convert
) -> ColumnElement[bool] | None:
    operator = criterion.operator
    if operator in ("eq", "is"):
        return column == convert(criterion, 0)
    if operator in ("ne", "is_not"):
        return column != convert(criterion, 0)
    if operator in ("lt", "before"):
        return column < convert(criterion, 0)
    if operator in ("lte", "at_most"):
        return column <= convert(criterion, 0)
    if operator in ("gt", "after"):
        return column > convert(criterion, 0)
    if operator in ("gte", "at_least"):
        return column >= convert(criterion, 0)
    if operator in ("between", "number_between"):
        low, high = convert(criterion, 0), convert(criterion, 1)
        if low > high:
            low, high = high, low
        return column.between(low, high)
    return None


# ---------------------------------------------------------------------------
# Identity targets
# ---------------------------------------------------------------------------


def _identity_clause(criterion: Criterion) -> ColumnElement[bool]:
    target, operator = criterion.target, criterion.operator

    if target == "__favourite__":
        if operator == "is_true":
            return Specimen.is_favourite == 1
        if operator == "is_false":
            return Specimen.is_favourite == 0
        raise FilterError(f"a favourite can only be yes or no, not {operator!r}")

    if target == "__subcollection__":
        name_column = (
            select(Subcollection.name)
            .where(Subcollection.id == Specimen.subcollection_id)
            .scalar_subquery()
        )
        clause = _text_clause(name_column, criterion)
        if clause is None:
            raise FilterError(f"a subcollection cannot be asked {operator!r}")
        return clause

    column = {
        "__id__": Specimen.inventory_code,
        "__name__": Specimen.display_name,
        "__status__": Specimen.status,
    }[target]

    if operator == "empty":
        return or_(column.is_(None), column == "")
    if operator == "not_empty":
        return and_(column.is_not(None), column != "")

    if target == "__status__" and operator in ("is", "is_not", "is_any_of"):
        wanted = [entry.strip() for entry in criterion.values if entry.strip()]
        for status in wanted:
            if status not in C.SPECIMEN_STATUSES:
                raise FilterError(
                    f"unknown status {status!r}; expected one of "
                    f"{', '.join(C.SPECIMEN_STATUSES)}"
                )

    clause = _text_clause(column, criterion)
    if clause is None:
        raise FilterError(f"{target} cannot be asked {operator!r}")
    return clause


# ---------------------------------------------------------------------------
# Field targets
# ---------------------------------------------------------------------------


def _field_exists(model: Any, field: FieldDefinition, *extra: ColumnElement[bool]):
    """``EXISTS`` over one coin's values for one field."""
    return (
        select(literal(1))
        .where(
            model.specimen_id == Specimen.id,
            model.field_definition_id == field.id,
            *extra,
        )
        .exists()
    )


#: The positive operator behind each negative one, used to build "none of its values are that".
OPPOSITES = {"is_not": "is", "not_contains": "contains", "ne": "eq"}


def _negated_field_clause(
    model: Any,
    field: FieldDefinition,
    criterion: Criterion,
    column: ColumnElement[Any],
    operator: str,
    convert=None,
):
    """"Is not" as *has a value, and none of its values are that*.

    Two decisions in one clause.

    A coin holding two rulers must not match "ruler is not Victoria" merely because its second
    ruler is Albert, so the exclusion is ``NOT EXISTS`` over all of the field's values rather
    than a comparison against one of them.

    A coin with no ruler recorded at all is also excluded, which is the less obvious half. A
    criterion is a statement about what the field contains, and a coin that has nothing recorded
    makes no such statement; treating absence as "not Victoria" quietly fills a negative filter
    with blank rows, which is the opposite of narrowing a collection down. Wanting the blanks
    too is expressible, and reads as what it is: a group matching *any* of "is empty" or
    "is not Victoria".
    """
    positive = Criterion(
        target=criterion.target, operator=OPPOSITES[operator], values=criterion.values
    )
    inner = (
        _numeric_clause(column, positive, convert)
        if convert is not None
        else _text_clause(column, positive)
    )
    return and_(
        _field_exists(model, field),
        not_(_field_exists(model, field, inner)),
    )


def _field_value(criterion: Criterion, field: FieldDefinition, position: int) -> float:
    """Convert the user's typed value into what the column actually stores.

    A weight is kept in grams and money in minor units, so "5 g" and "£1.50" have to go through
    the field type's own parser rather than being read as bare numbers. Anything the parser
    rejects falls back to a plain number, which is what a user filtering on ``12`` means.
    """
    raw = criterion.values[position] if len(criterion.values) > position else ""
    data_type = field.data_type
    config = dict(get_field_type(data_type).default_config)
    try:
        columns = parse_value(data_type, raw, config)
    except Exception:  # noqa: BLE001 - a filter value is not required to be well-formed
        return _number(criterion, position)
    for name in ("value", "amount_minor", "sort_value"):
        if columns.get(name) is not None:
            return float(columns[name])
    return _number(criterion, position)


def _date_clause(criterion: Criterion, field: FieldDefinition):
    """Dates are spans, so a year matches when it falls inside one."""
    model = FieldValueDate
    operator = criterion.operator

    if operator == "in_year":
        year = _year(criterion)
        return _field_exists(
            model, field, model.year_start <= year, model.year_end >= year
        )
    if operator == "between_years":
        low, high = _year(criterion, 0), _year(criterion, 1)
        if low > high:
            low, high = high, low
        # Any overlap counts: a coin dated 1736-1795 is "between 1750 and 1760".
        return _field_exists(model, field, model.year_start <= high, model.year_end >= low)
    if operator == "before":
        return _field_exists(model, field, model.year_end < _year(criterion))
    if operator == "after":
        return _field_exists(model, field, model.year_start > _year(criterion))
    if operator == "in_decade":
        decade = _year(criterion) // 10 * 10
        return _field_exists(
            model, field, model.year_start <= decade + 9, model.year_end >= decade
        )
    if operator == "in_century":
        year = _year(criterion)
        start = (year // 100) * 100
        return _field_exists(
            model, field, model.year_start <= start + 99, model.year_end >= start
        )
    if operator == "is_circa":
        return _field_exists(model, field, model.precision == "circa")
    if operator == "unknown":
        return _field_exists(model, field, model.precision == "unknown")
    raise FilterError(f"a date cannot be asked {operator!r}")


def _field_clause(criterion: Criterion, session: Session) -> ColumnElement[bool]:
    key = criterion.field_key or ""
    field = session.scalar(select(FieldDefinition).where(FieldDefinition.key == key))
    if field is None:
        raise FilterError(f"there is no column called {key!r}")

    field_type = get_field_type(field.data_type)
    if field_type.storage is None:
        raise FilterError(f"{field.label} holds nothing that can be filtered")
    model = VALUE_MODELS[field_type.storage]
    operator = criterion.operator

    if operator not in field_type.filter_operators:
        raise FilterError(
            f"{field.label} cannot be asked “{operator}”; it accepts "
            f"{', '.join(field_type.filter_operators)}"
        )

    if operator == "empty":
        return not_(_field_exists(model, field))
    if operator == "not_empty":
        return _field_exists(model, field)

    if field.data_type == "date":
        return _date_clause(criterion, field)

    if field.data_type == "boolean":
        if operator == "is_true":
            return _field_exists(model, field, model.value == 1)
        if operator == "is_false":
            return _field_exists(model, field, model.value == 0)
        raise FilterError(f"a yes/no column cannot be asked {operator!r}")

    column = getattr(model, "amount_minor", None) if field.data_type == "money" else None
    column = column if column is not None else model.value

    if field_type.storage == "text":
        clause = _text_clause(column, criterion)
        if clause is None:
            raise FilterError(f"{field.label} cannot be asked {operator!r}")
        if operator in ("is_not", "not_contains"):
            return _negated_field_clause(model, field, criterion, column, operator)
        return _field_exists(model, field, clause)

    def convert(crit: Criterion, position: int) -> float:
        return _field_value(crit, field, position)

    clause = _numeric_clause(column, criterion, convert)
    if clause is None:
        raise FilterError(f"{field.label} cannot be asked {operator!r}")
    if operator == "ne":
        return _negated_field_clause(model, field, criterion, column, operator, convert)
    return _field_exists(model, field, clause)


# ---------------------------------------------------------------------------
# The special systems
# ---------------------------------------------------------------------------


def _catalogue_clause(criterion: Criterion) -> ColumnElement[bool]:
    operator, value = criterion.operator, _first(criterion.values)

    def exists(*extra: ColumnElement[bool]):
        return (
            select(literal(1))
            .where(CatalogReference.specimen_id == Specimen.id, *extra)
            .exists()
        )

    in_catalogue = (
        select(Catalog.id).where(func.lower(Catalog.code) == value.lower()).scalar_subquery()
    )

    if operator == "empty":
        return not_(exists())
    if operator == "not_empty":
        return exists()
    if operator == "in_catalogue":
        return exists(CatalogReference.catalog_id.in_(in_catalogue))
    if operator == "not_in_catalogue":
        return not_(exists(CatalogReference.catalog_id.in_(in_catalogue)))
    if operator == "number_is":
        return exists(func.lower(CatalogReference.number_raw) == value.lower())
    if operator == "number_contains":
        return exists(_like(CatalogReference.number_raw, f"%{value}%"))
    if operator == "number_between":
        low, high = (entry.strip() for entry in (criterion.values + ("", ""))[:2])
        return exists(
            CatalogReference.sort_segments >= low.lower(),
            CatalogReference.sort_segments <= high.lower() + "\uffff",
        )
    raise FilterError(f"a catalogue number cannot be asked {operator!r}")


def _grade_clause(criterion: Criterion) -> ColumnElement[bool]:
    operator, value = criterion.operator, _first(criterion.values)

    def exists(*extra: ColumnElement[bool]):
        return select(literal(1)).where(SpecimenGrade.specimen_id == Specimen.id, *extra).exists()

    if operator == "empty":
        return not_(exists())
    if operator == "not_empty":
        return exists()
    if operator == "graded_by":
        return exists(
            or_(
                func.lower(SpecimenGrade.assigned_by) == value.lower(),
                func.lower(SpecimenGrade.source) == value.lower(),
            )
        )
    if operator in ("at_least", "at_most", "between", "eq", "ne"):
        clause = _numeric_clause(SpecimenGrade.normalised, criterion, _number)
        if clause is None:
            raise FilterError(f"a grade cannot be asked {operator!r}")
        return exists(clause)
    if operator in ("has_modifier", "is_problem"):
        modifier_condition = (
            GradeModifier.kind == "detail"
            if operator == "is_problem"
            else or_(
                func.lower(GradeModifier.code) == value.lower(),
                func.lower(GradeModifier.label) == value.lower(),
            )
        )
        link = (
            select(literal(1))
            .where(
                SpecimenGradeModifier.specimen_grade_id == SpecimenGrade.id,
                SpecimenGradeModifier.grade_modifier_id == GradeModifier.id,
                modifier_condition,
            )
            .exists()
        )
        return exists(link)
    raise FilterError(f"a grade cannot be asked {operator!r}")


def _certification_clause(criterion: Criterion) -> ColumnElement[bool]:
    operator, value = criterion.operator, _first(criterion.values)

    def exists(*extra: ColumnElement[bool]):
        return (
            select(literal(1))
            .where(
                Certification.specimen_id == Specimen.id,
                Certification.status == "current",
                *extra,
            )
            .exists()
        )

    company = (
        select(GradingCompany.id)
        .where(func.lower(GradingCompany.code) == value.lower())
        .scalar_subquery()
    )

    if operator == "empty":
        return not_(exists())
    if operator == "not_empty":
        return exists()
    if operator == "certified_by":
        return exists(Certification.grading_company_id.in_(company))
    if operator == "not_certified_by":
        return not_(exists(Certification.grading_company_id.in_(company)))
    if operator == "number_is":
        return exists(func.lower(Certification.cert_number) == value.lower())
    raise FilterError(f"a certification cannot be asked {operator!r}")


def _link_clause(criterion: Criterion) -> ColumnElement[bool]:
    operator, value = criterion.operator, _first(criterion.values)

    def exists(*extra: ColumnElement[bool]):
        return select(literal(1)).where(ExternalLink.specimen_id == Specimen.id, *extra).exists()

    if operator == "empty":
        return not_(exists())
    if operator == "not_empty":
        return exists()
    if operator == "of_kind":
        return exists(func.lower(ExternalLink.kind) == value.lower())
    raise FilterError(f"a link cannot be asked {operator!r}")


# ---------------------------------------------------------------------------
# Assembling the tree
# ---------------------------------------------------------------------------


def criterion_clause(criterion: Criterion, session: Session) -> ColumnElement[bool]:
    """One criterion as a SQL condition on ``specimen``."""
    criterion.validate()
    target = criterion.target
    if target in ("__id__", "__name__", "__status__", "__subcollection__", "__favourite__"):
        return _identity_clause(criterion)
    if target.startswith("field:"):
        return _field_clause(criterion, session)
    if target == "catalogues":
        return _catalogue_clause(criterion)
    if target == "grades":
        return _grade_clause(criterion)
    if target == "certifications":
        return _certification_clause(criterion)
    if target == "links":
        return _link_clause(criterion)
    raise FilterError(f"unknown filter target {target!r}")


def compile_filter(
    group: FilterGroup | None, session: Session
) -> ColumnElement[bool] | None:
    """The whole tree as one condition, or ``None`` when nothing is being filtered."""
    if group is None or group.is_empty():
        return None
    parts = [criterion_clause(criterion, session) for criterion in group.criteria]
    parts += [
        clause
        for clause in (compile_filter(nested, session) for nested in group.groups)
        if clause is not None
    ]
    if not parts:
        return None
    combined = and_(*parts) if group.match == "all" else or_(*parts)
    return not_(combined) if group.negate else combined


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------


def _code_expression() -> ColumnElement[Any]:
    """Order identifiers the way a person reads them: 2 before 10, blanks last.

    Mirrors what the grid did in Python, so a multi-column sort done in SQL agrees with a
    single-column sort done in memory.
    """
    code = Specimen.inventory_code
    digits_only = and_(code.is_not(None), code != "", not_(code.op("GLOB")("*[^0-9]*")))
    return case((digits_only, cast(code, Float)), else_=None)


def _field_sort_expression(key: str, session: Session) -> ColumnElement[Any] | None:
    field = session.scalar(select(FieldDefinition).where(FieldDefinition.key == key))
    if field is None:
        return None
    field_type = get_field_type(field.data_type)
    if field_type.storage is None or field_type.sort_column is None:
        raise FilterError(f"{field.label} cannot be sorted")
    model = VALUE_MODELS[field_type.storage]

    column_name = field_type.sort_column
    if hasattr(model, "sort_value") and (
        field.data_type == "date" or _numeric_sort(field)
    ):
        column_name = "sort_value"
    column = getattr(model, column_name)
    if column_name == "value" and field_type.storage == "text":
        column = func.lower(column)

    return (
        select(column)
        .where(
            model.specimen_id == Specimen.id,
            model.field_definition_id == field.id,
            model.seq == 0,
        )
        .limit(1)
        .scalar_subquery()
    )


def _numeric_sort(field: FieldDefinition) -> bool:
    import json

    try:
        return bool(json.loads(field.config_json or "{}").get("numeric_sort"))
    except (TypeError, ValueError):
        return False


def _system_sort_expression(target: str, catalogue: str | None) -> ColumnElement[Any]:
    """How a special-system column orders itself."""
    if target == "grades":
        # The calculated value, not the label: it is what makes MS63 comparable with gVF, and
        # what puts AU Details immediately below AU rather than at the bottom.
        return (
            select(SpecimenGrade.normalised)
            .where(SpecimenGrade.specimen_id == Specimen.id)
            .order_by(SpecimenGrade.rank, SpecimenGrade.id)
            .limit(1)
            .scalar_subquery()
        )
    if target == "catalogues":
        query = select(CatalogReference.sort_segments).where(
            CatalogReference.specimen_id == Specimen.id
        )
        if catalogue:
            query = query.where(
                CatalogReference.catalog_id.in_(
                    select(Catalog.id)
                    .where(func.lower(Catalog.code) == catalogue.lower())
                    .scalar_subquery()
                )
            )
        return (
            query.order_by(CatalogReference.rank, CatalogReference.id).limit(1).scalar_subquery()
        )
    if target == "certifications":
        return (
            select(func.lower(GradingCompany.code))
            .where(
                Certification.specimen_id == Specimen.id,
                Certification.status == "current",
                GradingCompany.id == Certification.grading_company_id,
            )
            .order_by(Certification.rank, Certification.id)
            .limit(1)
            .scalar_subquery()
        )
    if target == "links":
        # A count, because that is what the column shows by default.
        return (
            select(func.count(ExternalLink.id))
            .where(ExternalLink.specimen_id == Specimen.id)
            .scalar_subquery()
        )
    raise FilterError(f"{target} cannot be sorted")


def sort_expression(
    key: SortKey, session: Session, *, catalogue: str | None = None
) -> ColumnElement[Any] | None:
    """The expression one sort key orders by, or ``None`` if the column has gone."""
    target = key.target
    if target == "__id__":
        return _code_expression()
    if target == "__name__":
        return func.lower(Specimen.display_name)
    if target == "__status__":
        return Specimen.status
    if target == "__subcollection__":
        return (
            select(func.lower(Subcollection.name))
            .where(Subcollection.id == Specimen.subcollection_id)
            .scalar_subquery()
        )
    if target.startswith("field:"):
        return _field_sort_expression(target.removeprefix("field:"), session)
    return _system_sort_expression(target, catalogue)


def order_by_clauses(
    keys: Sequence[SortKey], session: Session, *, catalogues: dict[str, str] | None = None
) -> list[ColumnElement[Any]]:
    """``ORDER BY`` terms for a list of sort keys.

    Missing values sort last whichever direction is chosen: a blank is not smaller than
    everything, it is simply absent, and burying blanks at the top of a descending sort would be
    worse than useless.
    """
    catalogues = catalogues or {}
    clauses: list[ColumnElement[Any]] = []
    for key in keys:
        expression = sort_expression(key, session, catalogue=catalogues.get(key.target))
        if expression is None:
            continue
        clauses.append(expression.is_(None))
        clauses.append(expression.desc() if key.descending else expression.asc())
        if key.target == "__id__":
            # Two coins whose identifiers are not numeric still need a stable order.
            text_code = func.lower(Specimen.inventory_code)
            clauses.append(text_code.desc() if key.descending else text_code.asc())
    clauses.append(Specimen.id)
    return clauses
