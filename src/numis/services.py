"""The service layer: everything the interface calls.

All business logic lives here rather than in the models, so a GUI, a CLI and the tests
exercise identical code paths. Nothing in this module imports Qt or touches the filesystem.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import Select, delete, func, select, text
from sqlalchemy.orm import Session

from . import catalogs, grading, search
from . import constants as C
from .columns import DEFAULT_DISPLAY, ColumnDisplay, pick
from .errors import BindingNotSet, ConversionError, FieldParseError, NumisError, Warning_
from .fields import format_value, get_field_type, parse_value
from .models import (
    VALUE_MODELS,
    Catalog,
    CatalogReference,
    Certification,
    ExternalLink,
    FeatureBinding,
    FieldDefinition,
    FieldGroup,
    FieldValueDate,
    FieldValueText,
    GradeLevel,
    GradeModifier,
    GradeScale,
    GradingCompany,
    LibraryMeta,
    SavedView,
    Specimen,
    SpecimenEvent,
    SpecimenGrade,
    SpecimenGradeModifier,
    SpecimenSearch,
    Subcollection,
    SubcollectionBlock,
    Tag,
)
from .sqltypes import utcnow


@dataclass(frozen=True)
class Column:
    """A column in a table view."""

    key: str
    label: str
    #: ``field``, or one of the special systems: ``catalogues``, ``grades``, ...
    kind: str = "field"
    field_id: int | None = None
    data_type: str | None = None
    #: For a special system, how much of it this column shows. Unused for ordinary fields.
    display: ColumnDisplay = DEFAULT_DISPLAY


def _slugify(name: str) -> str:
    return "-".join(part for part in "".join(
        char.lower() if char.isalnum() else " " for char in name
    ).split())


class CollectionService:
    """Operations on one open library.

    Callers supply the session so a caller can group several operations into one
    transaction — which is what makes bulk edit a single undoable action.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.warnings: list[Warning_] = []

    # -- library ----------------------------------------------------------

    @property
    def meta(self) -> LibraryMeta:
        meta = self.session.scalar(select(LibraryMeta).where(LibraryMeta.id == 1))
        if meta is None:  # pragma: no cover
            raise NumisError("library_meta row is missing")
        return meta

    @property
    def currency_decimals(self) -> int:
        return self.meta.currency_decimals

    def warn(self, code: str, message: str, **context: Any) -> Warning_:
        """Record a non-blocking concern. Never raises; see docs/design/01, Part 1.8."""
        warning = Warning_(code=code, message=message, **context)
        self.warnings.append(warning)
        return warning

    # -- subcollections ---------------------------------------------------

    def create_subcollection(
        self, name: str, *, slug: str | None = None, naming_template: str = "", **kwargs: Any
    ) -> Subcollection:
        subcollection = Subcollection(
            name=name, slug=slug or _slugify(name), naming_template=naming_template, **kwargs
        )
        self.session.add(subcollection)
        self.session.flush()
        return subcollection

    # -- fields -----------------------------------------------------------

    def create_field_group(self, key: str, label: str, sort_order: int = 0) -> FieldGroup:
        group = FieldGroup(key=key, label=label, sort_order=sort_order)
        self.session.add(group)
        self.session.flush()
        return group

    def create_field(
        self,
        key: str,
        label: str,
        data_type: str,
        *,
        config: dict[str, Any] | None = None,
        is_multi: bool = False,
        kind: str = "value",
        help_text: str | None = None,
    ) -> FieldDefinition:
        """Define a field. Library-wide; subcollections opt in separately."""
        field_type = get_field_type(data_type)
        if is_multi and not field_type.supports_multi:
            raise NumisError(f"{data_type} fields cannot hold multiple values")
        field = FieldDefinition(
            key=key,
            label=label,
            data_type=data_type,
            kind=kind,
            config_json=json.dumps(config or {}),
            is_multi=int(is_multi),
            help_text=help_text,
        )
        self.session.add(field)
        self.session.flush()
        return field

    def field_config(self, field: FieldDefinition) -> dict[str, Any]:
        config = dict(json.loads(field.config_json or "{}"))
        if field.data_type == "money":
            config.setdefault("decimals", self.currency_decimals)
            config.setdefault("symbol", self.meta.currency_symbol)
        return config

    # -- special-system column settings -----------------------------------

    def block_for(
        self, subcollection: Subcollection, block_kind: str
    ) -> SubcollectionBlock | None:
        """The block placing a special system in a subcollection, if it is placed at all."""
        return self.session.scalars(
            select(SubcollectionBlock).where(
                SubcollectionBlock.subcollection_id == subcollection.id,
                SubcollectionBlock.block_kind == block_kind,
                SubcollectionBlock.field_definition_id.is_(None),
            )
        ).first()

    def block_display(self, block: SubcollectionBlock) -> ColumnDisplay:
        """How this column shows itself. Defaults when nothing has been chosen."""
        return ColumnDisplay.from_json(block.config_json)

    def set_block_display(
        self, block: SubcollectionBlock, display: ColumnDisplay
    ) -> SubcollectionBlock:
        """Record how a column shows itself. Presentation only — no coin data changes."""
        block.config_json = display.to_json()
        self.session.flush()
        return block

    def show_field(
        self,
        subcollection: Subcollection,
        field: FieldDefinition,
        *,
        display_label: str | None = None,
        show_in_table: bool = False,
        sort_order: int = 0,
        group: FieldGroup | None = None,
        is_required: bool = False,
    ) -> SubcollectionBlock:
        """Add a field to a subcollection, optionally under a different label.

        The same field shown in two subcollections under two labels is what makes them merge
        into one column in the master view: the merge is identity, not name matching.
        """
        block = SubcollectionBlock(
            subcollection_id=subcollection.id,
            block_kind="field",
            field_definition_id=field.id,
            display_label=display_label,
            show_in_table=int(show_in_table),
            sort_order=sort_order,
            group_id=group.id if group else None,
            is_required=int(is_required),
        )
        self.session.add(block)
        self.session.flush()
        return block

    def show_special_block(
        self,
        subcollection: Subcollection,
        block_kind: str,
        *,
        display_label: str | None = None,
        sort_order: int = 0,
        show_in_table: bool = False,
        config: dict[str, Any] | None = None,
    ) -> SubcollectionBlock:
        if block_kind == "field":
            raise NumisError("use show_field() for ordinary fields")
        if block_kind not in C.BLOCK_KINDS:
            raise NumisError(f"unknown block kind {block_kind!r}")
        block = SubcollectionBlock(
            subcollection_id=subcollection.id,
            block_kind=block_kind,
            display_label=display_label,
            sort_order=sort_order,
            show_in_table=int(show_in_table),
            config_json=json.dumps(config or {}),
        )
        self.session.add(block)
        self.session.flush()
        return block

    def hide_field(self, subcollection: Subcollection, field: FieldDefinition) -> None:
        """Remove a field from a subcollection. Values are retained.

        Re-adding the field brings the data straight back, which is what makes rearranging
        one's own schema safe rather than frightening.
        """
        self.session.execute(
            delete(SubcollectionBlock).where(
                SubcollectionBlock.subcollection_id == subcollection.id,
                SubcollectionBlock.field_definition_id == field.id,
            )
        )
        self.session.flush()

    def archive_field(self, field: FieldDefinition) -> None:
        """The default "delete": hidden everywhere, every value kept, reversible."""
        field.is_archived = 1
        self.session.flush()

    def restore_field(self, field: FieldDefinition) -> None:
        field.is_archived = 0
        self.session.flush()

    def purge_field(self, field: FieldDefinition) -> int:
        """Permanently delete a field and all its values. Returns the number destroyed."""
        count = self.count_values(field)
        self.session.delete(field)
        self.session.flush()
        return count

    def count_values(self, field: FieldDefinition) -> int:
        model = self._model_for(field)
        if model is None:
            return 0
        return int(
            self.session.scalar(
                select(func.count()).select_from(model).where(
                    model.field_definition_id == field.id
                )
            )
            or 0
        )

    def columns_for(self, subcollection: Subcollection, *, table_only: bool = True) -> list[Column]:
        """Columns for one subcollection, in the user's order, with their labels."""
        query = (
            select(SubcollectionBlock)
            .where(SubcollectionBlock.subcollection_id == subcollection.id)
            .order_by(SubcollectionBlock.sort_order, SubcollectionBlock.id)
        )
        columns: list[Column] = []
        for block in self.session.scalars(query):
            if table_only and not block.show_in_table:
                continue
            if block.block_kind == "field":
                field = block.field
                if field is None or field.is_archived:
                    continue
                columns.append(
                    Column(
                        key=field.key,
                        label=block.display_label or field.label,
                        kind="field",
                        field_id=field.id,
                        data_type=field.data_type,
                    )
                )
            else:
                columns.append(
                    Column(
                        key=block.block_kind,
                        label=block.display_label or block.block_kind.title(),
                        kind=block.block_kind,
                        display=self.block_display(block),
                    )
                )
        return columns

    def master_columns(
        self, subcollections: Sequence[Subcollection] | None = None, *, table_only: bool = True
    ) -> list[Column]:
        """Merged columns across subcollections.

        Fields shared between subcollections collapse into a single column labelled with the
        field's own canonical label, because they are the same field. Fields unique to one
        subcollection appear as their own columns, blank for rows from elsewhere.
        """
        if subcollections is None:
            subcollections = list(self.session.scalars(select(Subcollection)))
        merged: dict[str, Column] = {}
        for subcollection in subcollections:
            for column in self.columns_for(subcollection, table_only=table_only):
                if column.kind == "field":
                    field = self.session.get(FieldDefinition, column.field_id)
                    canonical = Column(
                        key=column.key,
                        label=field.label if field else column.label,
                        kind="field",
                        field_id=column.field_id,
                        data_type=column.data_type,
                    )
                    merged.setdefault(column.key, canonical)
                else:
                    # Two subcollections can show the same special system differently, and in
                    # the master view neither one's settings are more correct than the other's.
                    # Rather than let whichever loaded first decide, disagreement falls back to
                    # the plain defaults, which is at least explicable when the user sees it.
                    existing = merged.get(column.key)
                    if existing is None:
                        merged[column.key] = column
                    elif existing.display != column.display:
                        merged[column.key] = Column(
                            key=existing.key,
                            label=existing.label,
                            kind=existing.kind,
                            display=DEFAULT_DISPLAY,
                        )
        return list(merged.values())

    # -- specimens --------------------------------------------------------

    def next_inventory_code(self) -> str:
        """The lowest unused whole number, as text.

        Deleted specimens still count: their codes remain reserved, because reusing the
        number of a coin sitting in the Trash would make the two indistinguishable in any
        note, label or export that already mentions it.
        """
        used = {
            int(code)
            for code in self.session.scalars(
                select(Specimen.inventory_code).where(Specimen.inventory_code.is_not(None))
            )
            if code and code.isdigit()
        }
        candidate = 1
        while candidate in used:
            candidate += 1
        return str(candidate)

    def inventory_code_owner(self, code: str) -> Specimen | None:
        """The specimen already using ``code``, if any."""
        return self.session.scalar(
            select(Specimen).where(Specimen.inventory_code == code)
        )

    def set_inventory_code(
        self, specimen: Specimen, code: str | None, *, reuse_from_trash: bool = False
    ) -> Specimen | None:
        """Set a specimen's identifier.

        A code held by a coin still in use is refused. A code held by a coin in the Trash may
        be reused when ``reuse_from_trash`` is set: the deleted coin releases it and is given a
        fresh one if it is ever restored. Returns the coin that released a code, if any, so the
        caller can undo both halves.
        """
        cleaned = (code or "").strip() or None
        released: Specimen | None = None
        if cleaned is not None:
            owner = self.inventory_code_owner(cleaned)
            if owner is not None and owner.id != specimen.id:
                if owner.deleted_at is None:
                    raise NumisError(
                        f"ID {cleaned!r} is already used by "
                        f"{owner.display_name or 'another coin'}"
                    )
                if not reuse_from_trash:
                    raise NumisError(
                        f"ID {cleaned!r} belongs to a coin in the Trash "
                        f"({owner.display_name or 'unnamed'})"
                    )
                owner.inventory_code = None
                released = owner
                self.session.flush()
        specimen.inventory_code = cleaned
        self.session.flush()
        return released

    def set_display_name(self, specimen: Specimen, name: str) -> None:
        """Set a name by hand, or clear it to go back to the naming template."""
        cleaned = name.strip()
        specimen.display_name = cleaned
        specimen.display_name_manual = 1 if cleaned else 0
        if not cleaned:
            self.refresh_display_name(specimen)
        self.session.flush()

    def set_status(self, specimen: Specimen, status: str) -> None:
        """Set the current state of a coin.

        Directly editable, and also derived whenever a ledger entry is added: recording a sale
        sets it to ``sold``. Editing it by hand is for the case where you want to mark a coin
        without recording a transaction.
        """
        if status not in C.SPECIMEN_STATUSES:
            raise NumisError(
                f"unknown status {status!r}; expected one of {', '.join(C.SPECIMEN_STATUSES)}"
            )
        specimen.status = status
        self.session.flush()

    def move_specimens(
        self, specimens: Iterable[Specimen], subcollection: Subcollection
    ) -> int:
        """Move specimens into another subcollection.

        The values they already hold are untouched. A field the new subcollection does not
        show simply stops being displayed; it is not deleted, so moving a coin back restores
        the full picture.
        """
        moved = 0
        for specimen in specimens:
            if specimen.subcollection_id != subcollection.id:
                specimen.subcollection_id = subcollection.id
                moved += 1
        self.session.flush()
        return moved

    def subcollection_by_name(self, name: str) -> Subcollection | None:
        cleaned = name.strip().lower()
        for subcollection in self.session.scalars(select(Subcollection)):
            if subcollection.name.strip().lower() == cleaned:
                return subcollection
        return None

    def add_specimen(
        self,
        subcollection: Subcollection,
        *,
        values: dict[str, Any] | None = None,
        display_name: str = "",
        inventory_code: str | None = None,
        status: str = "owned",
        auto_code: bool = True,
    ) -> Specimen:
        specimen = Specimen(
            subcollection_id=subcollection.id,
            display_name=display_name,
            inventory_code=inventory_code
            if inventory_code is not None or not auto_code
            else self.next_inventory_code(),
            status=status,
        )
        self.session.add(specimen)
        self.session.flush()
        if display_name:
            specimen.display_name_manual = 1
        if values:
            self.set_values(specimen, values)
        if not display_name:
            self.refresh_display_name(specimen, subcollection)
        return specimen

    def bulk_add(
        self,
        subcollection: Subcollection,
        count: int,
        *,
        values: dict[str, Any] | None = None,
        display_name: str = "",
        status: str = "owned",
    ) -> list[Specimen]:
        """Create ``count`` separate specimens from one set of values.

        This is the primary way shared data gets entered: there is no coin-type layer to
        inherit from, so bulk add is what replaces inheritance rather than being a
        convenience added later.
        """
        if count < 1:
            raise NumisError("count must be at least 1")
        return [
            self.add_specimen(
                subcollection, values=values, display_name=display_name, status=status
            )
            for _ in range(count)
        ]

    def bulk_edit(self, specimens: Iterable[Specimen], values: dict[str, Any]) -> int:
        """Apply the same values to many specimens. One transaction, one undo step."""
        changed = 0
        for specimen in specimens:
            self.set_values(specimen, values)
            changed += 1
        return changed

    def soft_delete(self, specimen: Specimen) -> None:
        """Move to Trash. Retained indefinitely; there is no automatic purge."""
        specimen.deleted_at = utcnow()
        self.session.flush()

    def restore(self, specimen: Specimen) -> str | None:
        """Bring a coin back from the Trash.

        If its identifier was reused while it was deleted it is given a fresh one, and that new
        code is returned so the interface can say so. Refusing to restore would be worse: the
        coin's data matters more than its number.
        """
        specimen.deleted_at = None
        reassigned: str | None = None
        if specimen.inventory_code is None:
            reassigned = self.next_inventory_code()
            specimen.inventory_code = reassigned
        self.session.flush()
        return reassigned

    def purge(self, specimen: Specimen) -> None:
        """Permanently delete, cascading to values, references, grades, links and events."""
        self.session.delete(specimen)
        self.session.flush()

    def live_specimens(
        self,
        subcollection: Subcollection | None = None,
        *,
        include_deleted: bool = False,
        include_disposed: bool = True,
    ) -> Select[tuple[Specimen]]:
        """Specimens, excluding the Trash unless asked otherwise.

        ``include_disposed`` covers coins that have left the collection — sold, traded, given
        away, lost or stolen. They remain in the database with their full history; this only
        decides whether they are listed.
        """
        query = select(Specimen)
        if not include_deleted:
            query = query.where(Specimen.deleted_at.is_(None))
        if not include_disposed:
            query = query.where(Specimen.status.notin_(C.DISPOSED_STATUSES))
        if subcollection is not None:
            query = query.where(Specimen.subcollection_id == subcollection.id)
        return query

    def refresh_display_name(
        self, specimen: Specimen, subcollection: Subcollection | None = None
    ) -> str:
        """Render ``display_name`` from the subcollection's template.

        The template refers to field keys, e.g. ``{country} {denomination} {date}``. A missing
        field renders as empty rather than raising, so a half-entered coin still has a name.
        """
        if specimen.display_name_manual:
            return specimen.display_name
        subcollection = subcollection or self.session.get(Subcollection, specimen.subcollection_id)
        template = (subcollection.naming_template if subcollection else "") or ""
        if not template:
            return specimen.display_name

        rendered = template
        for key in _template_keys(template):
            field = self.field_by_key(key)
            text_value = ""
            if field is not None:
                text_value = self.display(specimen, field)
            rendered = rendered.replace("{" + key + "}", text_value)
        specimen.display_name = " ".join(rendered.split())
        self.session.flush()
        return specimen.display_name

    def field_by_key(self, key: str) -> FieldDefinition | None:
        return self.session.scalar(select(FieldDefinition).where(FieldDefinition.key == key))

    # -- field values -----------------------------------------------------

    def _model_for(self, field: FieldDefinition) -> type | None:
        storage = get_field_type(field.data_type).storage
        return VALUE_MODELS.get(storage) if storage else None

    def set_value(
        self, specimen: Specimen, field: FieldDefinition | str, raw: Any, *, seq: int = 0
    ) -> Any:
        """Parse and store one value, replacing any existing value at ``seq``."""
        field = self._resolve_field(field)
        model = self._model_for(field)
        if model is None:
            raise NumisError(f"{field.data_type} fields cannot store values directly")
        if seq and not field.is_multi:
            raise NumisError(f"field {field.key!r} does not hold multiple values")

        columns = parse_value(field.data_type, raw, self.field_config(field))
        existing = self.session.scalar(
            select(model).where(
                model.field_definition_id == field.id,
                model.specimen_id == specimen.id,
                model.seq == seq,
            )
        )
        if existing is None:
            existing = model(field_definition_id=field.id, specimen_id=specimen.id, seq=seq)
            self.session.add(existing)
        for name, value in columns.items():
            setattr(existing, name, value)
        self.session.flush()
        return existing

    def set_values(self, specimen: Specimen, values: dict[str, Any]) -> None:
        """Set several values by field key. Unparseable input raises, naming the field."""
        for key, raw in values.items():
            field = self.field_by_key(key)
            if field is None:
                raise NumisError(f"no field with key {key!r}")
            self.set_value(specimen, field, raw)

    def get_value(
        self, specimen: Specimen, field: FieldDefinition | str, *, seq: int = 0
    ) -> Any | None:
        field = self._resolve_field(field)
        model = self._model_for(field)
        if model is None:
            return None
        return self.session.scalar(
            select(model).where(
                model.field_definition_id == field.id,
                model.specimen_id == specimen.id,
                model.seq == seq,
            )
        )

    def display(self, specimen: Specimen, field: FieldDefinition | str, *, seq: int = 0) -> str:
        """The formatted value for display, or an empty string when unset."""
        field = self._resolve_field(field)
        row = self.get_value(specimen, field, seq=seq)
        if row is None:
            return ""
        columns = {
            name: getattr(row, name)
            for name in ("value", "amount_minor", "as_of", "display")
            if hasattr(row, name)
        }
        return format_value(field.data_type, columns, self.field_config(field))

    def raw_columns(
        self, specimen: Specimen, field: FieldDefinition | str, *, seq: int = 0
    ) -> dict[str, Any] | None:
        """The stored column values for one value, or ``None`` when unset.

        Used by undo: restoring these exactly puts a value back as it was, including a
        manually chosen sort key that re-parsing the display text would lose.
        """
        field = self._resolve_field(field)
        row = self.get_value(specimen, field, seq=seq)
        if row is None:
            return None
        model = self._model_for(field)
        skip = {"id", "field_definition_id", "specimen_id", "seq", "_sa_instance_state"}
        return {
            name: getattr(row, name)
            for name in model.__table__.columns.keys()  # noqa: SIM118
            if name not in skip
        }

    def write_columns(
        self,
        specimen: Specimen,
        field: FieldDefinition | str,
        columns: dict[str, Any] | None,
        *,
        seq: int = 0,
    ) -> None:
        """Write stored column values directly, or delete the value when ``None``.

        Bypasses parsing on purpose: this is the inverse of :meth:`raw_columns` and exists
        for undo, not for user input.
        """
        field = self._resolve_field(field)
        model = self._model_for(field)
        if model is None:
            raise NumisError(f"{field.data_type} fields cannot store values")
        existing = self.get_value(specimen, field, seq=seq)
        if columns is None:
            if existing is not None:
                self.session.delete(existing)
                self.session.flush()
            return
        if existing is None:
            existing = model(field_definition_id=field.id, specimen_id=specimen.id, seq=seq)
            self.session.add(existing)
        for name, value in columns.items():
            setattr(existing, name, value)
        self.session.flush()

    def value_grid(
        self, specimens: Sequence[Specimen], fields: Sequence[FieldDefinition]
    ) -> dict[tuple[int, int], str]:
        """Formatted values for many specimens and fields at once.

        A table view asking for one value per cell would issue a query per cell; this loads
        each storage table once instead, which is what keeps scrolling responsive.
        """
        grid: dict[tuple[int, int], str] = {}
        if not specimens or not fields:
            return grid
        specimen_ids = [specimen.id for specimen in specimens]

        by_storage: dict[str, list[FieldDefinition]] = {}
        for field in fields:
            storage = get_field_type(field.data_type).storage
            if storage:
                by_storage.setdefault(storage, []).append(field)

        for storage, storage_fields in by_storage.items():
            model = VALUE_MODELS[storage]
            configs = {field.id: self.field_config(field) for field in storage_fields}
            types = {field.id: field.data_type for field in storage_fields}
            rows = self.session.scalars(
                select(model).where(
                    model.field_definition_id.in_([f.id for f in storage_fields]),
                    model.specimen_id.in_(specimen_ids),
                    model.seq == 0,
                )
            )
            for row in rows:
                columns = {
                    name: getattr(row, name)
                    for name in ("value", "amount_minor", "as_of", "display")
                    if hasattr(row, name)
                }
                grid[(row.specimen_id, row.field_definition_id)] = format_value(
                    types[row.field_definition_id], columns, configs[row.field_definition_id]
                )
        return grid

    def review_flags(
        self, specimens: Sequence[Specimen], fields: Sequence[FieldDefinition]
    ) -> set[tuple[int, int]]:
        """Cells whose sort position the application guessed or could not work out."""
        flagged: set[tuple[int, int]] = set()
        if not specimens or not fields:
            return flagged
        specimen_ids = [specimen.id for specimen in specimens]
        field_ids = [field.id for field in fields]
        for model in (FieldValueText, FieldValueDate):
            rows = self.session.scalars(
                select(model).where(
                    model.needs_review == 1,
                    model.field_definition_id.in_(field_ids),
                    model.specimen_id.in_(specimen_ids),
                )
            )
            for row in rows:
                flagged.add((row.specimen_id, row.field_definition_id))
        return flagged

    def set_sort_value(
        self, specimen: Specimen, field: FieldDefinition | str, sort_value: float, *, seq: int = 0
    ) -> Any:
        """Record a sort value chosen by the user.

        Marks the value ``manual`` and clears the review flag. A manual sort value is never
        overwritten by the parser afterwards, which is what makes the proposal mechanism
        trustworthy rather than annoying.
        """
        field = self._resolve_field(field)
        row = self.get_value(specimen, field, seq=seq)
        if row is None:
            raise NumisError(f"{field.key!r} has no value on this specimen")
        if not hasattr(row, "sort_value"):
            raise NumisError(f"{field.data_type} values do not carry a sort key")
        row.sort_value = float(sort_value)
        row.sort_source = "manual"
        row.needs_review = 0
        self.session.flush()
        return row

    def needs_review(
        self, subcollection: Subcollection | None = None
    ) -> list[tuple[int, str, str]]:
        """Values whose sort position the app guessed or could not work out.

        Returns ``(specimen_id, field_key, display)`` so the interface can offer a
        "confirm these" queue rather than nagging during entry.
        """
        found: list[tuple[int, str, str]] = []
        for model, value_column in ((FieldValueDate, "display"), (FieldValueText, "value")):
            query = (
                select(model, FieldDefinition.key)
                .join(FieldDefinition, FieldDefinition.id == model.field_definition_id)
                .join(Specimen, Specimen.id == model.specimen_id)
                .where(model.needs_review == 1, Specimen.deleted_at.is_(None))
            )
            if subcollection is not None:
                query = query.where(Specimen.subcollection_id == subcollection.id)
            for row, key in self.session.execute(query):
                found.append((row.specimen_id, key, getattr(row, value_column)))
        return found

    def sorted_by_field(
        self,
        field: FieldDefinition | str,
        *,
        subcollection: Subcollection | None = None,
        descending: bool = False,
        include_deleted: bool = False,
        include_disposed: bool = True,
    ) -> list[Specimen]:
        """Specimens ordered by one field, with missing values last.

        Missing values sort last in both directions: a blank is not "smaller than
        everything", it is simply absent, and burying it at the top of a descending sort
        would be worse than useless.
        """
        field = self._resolve_field(field)
        model = self._model_for(field)
        if model is None:
            raise NumisError(f"{field.data_type} fields cannot be sorted")
        field_type = get_field_type(field.data_type)
        if field_type.sort_column is None:
            raise NumisError(f"{field.data_type} fields are not sortable")

        # A field carrying a sort key is ordered by it, not by its text. This is the whole
        # point of the sort key: '10 wen' must follow '1 wen', and a date written
        # '1736-1795' must sort at 1765.5 rather than alphabetically under "1".
        sort_name = field_type.sort_column
        if hasattr(model, "sort_value") and (
            field.data_type == "date" or self.field_config(field).get("numeric_sort")
        ):
            sort_name = "sort_value"
        sort_attr = getattr(model, sort_name)
        # For text fields with numeric ordering enabled, fall back to the text itself when
        # no sort value has been decided, so the column is never randomly ordered.
        query = (
            self.live_specimens(
                subcollection,
                include_deleted=include_deleted,
                include_disposed=include_disposed,
            )
            .outerjoin(
                model,
                (model.specimen_id == Specimen.id)
                & (model.field_definition_id == field.id)
                & (model.seq == 0),
            )
            .order_by(
                sort_attr.is_(None),
                sort_attr.desc() if descending else sort_attr.asc(),
                Specimen.id,
            )
        )
        return list(self.session.scalars(query))

    def _resolve_field(self, field: FieldDefinition | str) -> FieldDefinition:
        if isinstance(field, FieldDefinition):
            return field
        resolved = self.field_by_key(field)
        if resolved is None:
            raise NumisError(f"no field with key {field!r}")
        return resolved

    def convert_field_type(
        self, field: FieldDefinition, new_type: str, *, new_key: str | None = None
    ) -> tuple[FieldDefinition, list[Warning_]]:
        """Change a field's type by add-convert-archive, never in place.

        A new field is created, values are converted into it, and the original is archived.
        Nothing is destroyed, the operation is reversible, and rows that fail conversion are
        reported rather than silently blanked.
        """
        source_model = self._model_for(field)
        if source_model is None:
            raise ConversionError(f"{field.data_type} fields hold no values to convert")

        replacement = self.create_field(
            key=new_key or f"{field.key}_{new_type}",
            label=field.label,
            data_type=new_type,
            is_multi=bool(field.is_multi),
        )
        problems: list[Warning_] = []
        rows = self.session.scalars(
            select(source_model).where(source_model.field_definition_id == field.id)
        )
        for row in rows:
            raw = getattr(row, "display", None) or getattr(row, "value", None)
            try:
                self.set_value(
                    self.session.get(Specimen, row.specimen_id), replacement, raw, seq=row.seq
                )
            except FieldParseError as exc:
                problems.append(
                    Warning_(
                        code="conversion_failed",
                        message=f"{raw!r} could not be read as {new_type}: {exc.reason}",
                        specimen_id=row.specimen_id,
                        field_key=field.key,
                    )
                )
        self.archive_field(field)
        self.warnings.extend(problems)
        return replacement, problems

    # -- catalogues -------------------------------------------------------

    def create_catalog(
        self, code: str, name: str, *, letter_prefix_order: str = "after", **kwargs: Any
    ) -> Catalog:
        catalog = Catalog(
            code=code, name=name, letter_prefix_order=letter_prefix_order, **kwargs
        )
        self.session.add(catalog)
        self.session.flush()
        return catalog

    def add_reference(
        self,
        specimen: Specimen,
        catalog: Catalog,
        number: str,
        *,
        rank: int | None = None,
        qualifier: str | None = None,
        certainty: str = "certain",
    ) -> CatalogReference:
        columns = catalogs.build_reference_columns(
            number,
            catalog_code=catalog.code,
            letter_prefix_order=catalog.letter_prefix_order,
        )
        reference = CatalogReference(
            catalog_id=catalog.id,
            specimen_id=specimen.id,
            rank=rank if rank is not None else self._next_rank(CatalogReference, specimen),
            qualifier=qualifier,
            certainty=certainty,
            **columns,
        )
        self.session.add(reference)
        self.session.flush()
        return reference

    def references_for(
        self, specimen: Specimen, catalog: Catalog | None = None
    ) -> list[CatalogReference]:
        query = select(CatalogReference).where(CatalogReference.specimen_id == specimen.id)
        if catalog is not None:
            query = query.where(CatalogReference.catalog_id == catalog.id)
        return list(
            self.session.scalars(query.order_by(CatalogReference.rank, CatalogReference.id))
        )

    # -- rendering a special-system column --------------------------------

    def special_cell(
        self, specimen: Specimen, kind: str, display: ColumnDisplay | None = None
    ) -> str:
        """What a special-system column shows for one coin.

        Lives here rather than in the table model so that the rules are testable without a GUI,
        and so an export or a label template renders a column exactly as the grid does.
        """
        display = display or DEFAULT_DISPLAY
        if kind == "catalogues":
            return self._catalogue_cell(specimen, display)
        if kind == "grades":
            return self._grade_cell(specimen, display)
        if kind == "certifications":
            return self._certification_cell(specimen, display)
        if kind == "links":
            return self._link_cell(specimen, display)
        return ""

    def _catalogue_cell(self, specimen: Specimen, display: ColumnDisplay) -> str:
        references = self.references_for(specimen)
        if display.mode == "only" and display.only:
            wanted = display.only.casefold()
            references = [
                reference
                for reference in references
                if (catalog := self.session.get(Catalog, reference.catalog_id)) is not None
                and catalog.code.casefold() == wanted
            ]
        parts = []
        for reference in pick(references, display):
            catalog = self.session.get(Catalog, reference.catalog_id)
            if catalog is not None and display.show_catalogue:
                parts.append(f"{catalog.code} {reference.number_raw}")
            else:
                parts.append(reference.number_raw)
        return display.separator.join(part for part in parts if part)

    def _grade_cell(self, specimen: Specimen, display: ColumnDisplay) -> str:
        grades = self.grades_for(specimen)
        if display.mode == "only" and display.only:
            # A grade has no company of its own — an opinion belongs to whoever gave it, which
            # may be a company, a dealer or the collector. So one filter matches either the
            # source ('tpg', 'seller') or the name recorded against it ('NGC', 'Bob Reis').
            wanted = display.only.casefold()
            grades = [
                grade
                for grade in grades
                if wanted in ((grade.source or "").casefold(), (grade.assigned_by or "").casefold())
            ]
        rendered = [grading.render(grade, display.grade_display) for grade in pick(grades, display)]
        return display.separator.join(part for part in rendered if part)

    def _certification_cell(self, specimen: Specimen, display: ColumnDisplay) -> str:
        certifications = self.current_certifications(specimen)
        if display.mode == "only" and display.only:
            wanted = display.only.casefold()
            certifications = [
                certification
                for certification in certifications
                if certification.company is not None
                and certification.company.code.casefold() == wanted
            ]
        parts = []
        for certification in pick(certifications, display):
            company = certification.company
            code = company.code if company else ""
            parts.append(f"{code} {certification.cert_number or ''}".strip())
        return display.separator.join(part for part in parts if part)

    def _link_cell(self, specimen: Specimen, display: ColumnDisplay) -> str:
        links = self.links_for(specimen)
        if display.mode == "only" and display.only:
            wanted = display.only.casefold()
            links = [link for link in links if (link.kind or "").casefold() == wanted]
        chosen = pick(links, display)
        if not chosen:
            return ""
        if display.show_labels:
            return display.separator.join(link.label or link.url for link in chosen)
        # A bare count by default: a URL is unreadable at column width, and what the collector
        # wants at a glance is whether there is anything to follow.
        return str(len(chosen))

    def links_for(self, specimen: Specimen) -> list[ExternalLink]:
        """A coin's links in the user's order of precedence."""
        return list(
            self.session.scalars(
                select(ExternalLink)
                .where(ExternalLink.specimen_id == specimen.id)
                .order_by(ExternalLink.rank, ExternalLink.sort_order, ExternalLink.id)
            )
        )

    def combined_catalogue_cell(self, specimen: Specimen, separator: str = " · ") -> str:
        """All catalogue numbers in one cell, as a combined column displays them."""
        parts = []
        for reference in self.references_for(specimen):
            catalog = self.session.get(Catalog, reference.catalog_id)
            parts.append(
                f"{catalog.code} {reference.number_raw}" if catalog else reference.number_raw
            )
        return separator.join(parts)

    def sorted_by_catalogue(
        self,
        catalog: Catalog,
        *,
        subcollection: Subcollection | None = None,
        descending: bool = False,
        include_deleted: bool = False,
        include_disposed: bool = True,
    ) -> list[Specimen]:
        """Specimens ordered by one catalogue's numbers.

        Coins with no reference in that catalogue go to the **bottom**, in both directions.
        This is what makes a combined catalogue column still sortable by a chosen catalogue.
        """
        query = (
            self.live_specimens(subcollection, include_deleted=include_deleted)
            .outerjoin(
                CatalogReference,
                (CatalogReference.specimen_id == Specimen.id)
                & (CatalogReference.catalog_id == catalog.id),
            )
            .order_by(
                CatalogReference.sort_segments.is_(None),
                CatalogReference.sort_segments.desc()
                if descending
                else CatalogReference.sort_segments.asc(),
                Specimen.id,
            )
        )
        return list(self.session.scalars(query))

    def specimens_in_catalogue_range(
        self, catalog: Catalog, low: str, high: str
    ) -> list[Specimen]:
        start, end = catalogs.range_bounds(
            low, high, catalog_code=catalog.code, letter_prefix_order=catalog.letter_prefix_order
        )
        query = (
            self.live_specimens()
            .join(CatalogReference, CatalogReference.specimen_id == Specimen.id)
            .where(
                CatalogReference.catalog_id == catalog.id,
                CatalogReference.sort_segments >= start,
                CatalogReference.sort_segments <= end,
            )
            .order_by(CatalogReference.sort_segments)
        )
        return list(self.session.scalars(query))

    # -- grading ----------------------------------------------------------

    def create_grade_scale(
        self, code: str, name: str, *, kind: str = "ordinal", **kwargs: Any
    ) -> GradeScale:
        scale = GradeScale(code=code, name=name, kind=kind, **kwargs)
        self.session.add(scale)
        self.session.flush()
        return scale

    def add_grade_level(
        self,
        scale: GradeScale,
        label: str,
        normalised: float,
        *,
        numeric_value: float | None = None,
        aliases: str | None = None,
        sort_order: int = 0,
    ) -> GradeLevel:
        level = GradeLevel(
            grade_scale_id=scale.id,
            label=label,
            normalised=normalised,
            numeric_value=numeric_value,
            aliases=aliases,
            sort_order=sort_order,
        )
        self.session.add(level)
        self.session.flush()
        return level

    def add_grade(
        self,
        specimen: Specimen,
        scale: GradeScale | None,
        grade_label: str,
        *,
        base_value: float | None = None,
        modifiers: Sequence[tuple[GradeModifier | str, str | None]]
        | Sequence[GradeModifier | str] = (),
        source: str = "self",
        assigned_by: str | None = None,
        hide_assigned_by: bool = False,
        assigned_on: date | None = None,
        detail_note: str | None = None,
        rank: int | None = None,
        notes: str | None = None,
    ) -> SpecimenGrade:
        """Record a grade.

        ``grade_label`` is whatever the user wants to see and ``base_value`` what it counts as.
        Nothing is looked up, so typing a grade that another coin already uses is ordinary rather
        than an error. ``modifiers`` accepts either plain modifiers or ``(modifier, detail)``
        pairs, the detail being what that one says: ``Harshly Cleaned``, ``Gold``, ``Brown``.
        """
        label = grade_label.strip()
        if not label:
            raise NumisError("a grade needs a label, for example MS63 or gVF")
        if base_value is None:
            base_value = grading.suggest_base_value(label)

        resolved = [self._resolve_modifier(entry) for entry in modifiers]
        grade = SpecimenGrade(
            specimen_id=specimen.id,
            grade_scale_id=scale.id if scale else None,
            grade_label=label,
            base_value=base_value,
            raw_text=label,
            normalised=grading.calculated_value(
                base_value, [modifier for modifier, _ in resolved]
            ),
            detail_note=detail_note,
            source=source,
            assigned_by=assigned_by,
            hide_assigned_by=int(hide_assigned_by),
            assigned_on=assigned_on,
            rank=rank if rank is not None else self._next_rank(SpecimenGrade, specimen),
            notes=notes,
        )
        self.session.add(grade)
        self.session.flush()
        for order, (modifier, detail) in enumerate(resolved):
            self.session.add(
                SpecimenGradeModifier(
                    specimen_grade_id=grade.id,
                    grade_modifier_id=modifier.id,
                    detail=detail,
                    sort_order=order,
                )
            )
        self.session.flush()
        self.refresh_grade_text(grade)
        return grade

    def update_grade(
        self,
        grade: SpecimenGrade,
        *,
        scale: GradeScale | None = None,
        grade_label: str | None = None,
        base_value: float | None = None,
        modifiers: Sequence[tuple[GradeModifier | str, str | None]] | None = None,
        source: str | None = None,
        assigned_by: str | None = None,
        hide_assigned_by: bool | None = None,
        detail_note: str | None = None,
    ) -> SpecimenGrade:
        """Change an existing grade. Only the arguments given are touched."""
        if grade_label is not None:
            label = grade_label.strip()
            if not label:
                raise NumisError("a grade needs a label")
            grade.grade_label = label
        if scale is not None:
            grade.grade_scale_id = scale.id
        if base_value is not None:
            grade.base_value = base_value
        if source is not None:
            grade.source = source
        if assigned_by is not None:
            grade.assigned_by = assigned_by or None
        if hide_assigned_by is not None:
            grade.hide_assigned_by = int(hide_assigned_by)
        if detail_note is not None:
            grade.detail_note = detail_note or None

        if modifiers is not None:
            for link in list(grade.modifier_links):
                self.session.delete(link)
            self.session.flush()
            for order, entry in enumerate(modifiers):
                modifier, detail = self._resolve_modifier(entry)
                self.session.add(
                    SpecimenGradeModifier(
                        specimen_grade_id=grade.id,
                        grade_modifier_id=modifier.id,
                        detail=detail,
                        sort_order=order,
                    )
                )
            self.session.flush()

        self.session.refresh(grade)
        self.refresh_grade_text(grade)
        return grade

    def refresh_grade_text(self, grade: SpecimenGrade) -> SpecimenGrade:
        """Recompute the cached display text and the comparable value."""
        grade.normalised = grading.calculated_value(grade.base_value, grade.modifiers)
        grade.raw_text = grading.render(grade, grading.GradeDisplay(modifier_details=False))
        self.session.flush()
        return grade

    def _resolve_modifier(
        self, entry: GradeModifier | str | tuple[GradeModifier | str, str | None]
    ) -> tuple[GradeModifier, str | None]:
        detail: str | None = None
        if isinstance(entry, tuple):
            entry, detail = entry
        if isinstance(entry, GradeModifier):
            return entry, detail
        found = self.session.scalar(select(GradeModifier).where(GradeModifier.code == entry))
        if found is None:
            raise NumisError(f"no grade modifier with code {entry!r}")
        return found, detail

    def _next_rank(self, model: type, specimen: Specimen) -> int:
        """One past the lowest-priority entry, so new items go to the back."""
        highest = self.session.scalar(
            select(func.max(model.rank)).where(model.specimen_id == specimen.id)
        )
        return (highest or 0) + 1

    def set_rank(self, row: Any, rank: int) -> None:
        """Move an entry up or down the order of precedence."""
        row.rank = max(1, int(rank))
        self.session.flush()

    def reorder(self, rows: Sequence[Any]) -> None:
        """Renumber a list of entries 1..n in the order given."""
        for position, row in enumerate(rows, start=1):
            row.rank = position
        self.session.flush()

    def primary_grade(self, specimen: Specimen) -> SpecimenGrade | None:
        """The grade a single-value column shows: the one ranked first."""
        return self.session.scalars(
            select(SpecimenGrade)
            .where(SpecimenGrade.specimen_id == specimen.id)
            .order_by(SpecimenGrade.rank, SpecimenGrade.id)
        ).first()

    def grades_for(self, specimen: Specimen) -> list[SpecimenGrade]:
        return list(
            self.session.scalars(
                select(SpecimenGrade)
                .where(SpecimenGrade.specimen_id == specimen.id)
                .order_by(SpecimenGrade.rank, SpecimenGrade.id)
            )
        )

    def grades_at_least(
        self, normalised: float, *, exclude_problems: bool = False
    ) -> list[SpecimenGrade]:
        """Grades at or above a position on the shared scale, across every standard."""
        query = select(SpecimenGrade).where(SpecimenGrade.normalised >= normalised)
        grades = list(self.session.scalars(query.order_by(SpecimenGrade.normalised.desc())))
        if exclude_problems:
            grades = [grade for grade in grades if not grading.is_problem_grade(grade)]
        return grades

    # -- grade modifiers --------------------------------------------------

    def create_grade_modifier(
        self,
        code: str,
        label: str,
        kind: str,
        normalised_delta: float = 0.0,
        *,
        abbreviation: str | None = None,
        issuer: str | None = None,
        attach_without_space: bool = False,
        notes: str | None = None,
    ) -> GradeModifier:
        if kind not in C.GRADE_MODIFIER_KINDS:
            raise NumisError(
                f"unknown modifier kind {kind!r}; expected one of "
                f"{', '.join(C.GRADE_MODIFIER_KINDS)}"
            )
        modifier = GradeModifier(
            code=code,
            label=label,
            kind=kind,
            normalised_delta=normalised_delta,
            abbreviation=abbreviation,
            issuer=issuer,
            attach_without_space=int(attach_without_space),
            notes=notes,
        )
        self.session.add(modifier)
        self.session.flush()
        return modifier

    def update_grade_modifier(self, modifier: GradeModifier, **changes: Any) -> GradeModifier:
        """Edit a modifier. Every grade using it is re-rendered, since its reading may change."""
        if "kind" in changes and changes["kind"] not in C.GRADE_MODIFIER_KINDS:
            raise NumisError(f"unknown modifier kind {changes['kind']!r}")
        for name, value in changes.items():
            if not hasattr(modifier, name):
                raise NumisError(f"grade modifiers have no {name!r}")
            setattr(modifier, name, int(value) if name == "attach_without_space" else value)
        self.session.flush()
        for grade in self.grades_using_modifier(modifier):
            self.session.expire(grade)
            self.refresh_grade_text(grade)
        return modifier

    def grades_using_modifier(self, modifier: GradeModifier) -> list[SpecimenGrade]:
        return list(
            self.session.scalars(
                select(SpecimenGrade)
                .join(
                    SpecimenGradeModifier,
                    SpecimenGradeModifier.specimen_grade_id == SpecimenGrade.id,
                )
                .where(SpecimenGradeModifier.grade_modifier_id == modifier.id)
            )
        )

    def modifier_usage(self, modifier: GradeModifier) -> int:
        return int(
            self.session.scalar(
                select(func.count())
                .select_from(SpecimenGradeModifier)
                .where(SpecimenGradeModifier.grade_modifier_id == modifier.id)
            )
            or 0
        )

    def delete_grade_modifier(self, modifier: GradeModifier, *, force: bool = False) -> int:
        """Delete a modifier. Returns how many grades it was taken off.

        Refuses while in use unless ``force``, because silently changing the grade of every coin
        carrying it would be a surprising way to lose information.
        """
        grades = self.grades_using_modifier(modifier)
        if grades and not force:
            raise NumisError(
                f"{modifier.label!r} is used by {len(grades)} grade(s); "
                "remove it from those first, or delete it anyway"
            )
        for link in list(
            self.session.scalars(
                select(SpecimenGradeModifier).where(
                    SpecimenGradeModifier.grade_modifier_id == modifier.id
                )
            )
        ):
            self.session.delete(link)
        self.session.flush()
        self.session.delete(modifier)
        self.session.flush()
        for grade in grades:
            # The link rows are gone, but the grade still holds the collection it loaded
            # earlier, so it has to be expired before its text is recomputed.
            self.session.expire(grade)
            self.refresh_grade_text(grade)
        return len(grades)

    def modifiers(self, kind: str | None = None) -> list[GradeModifier]:
        query = select(GradeModifier)
        if kind is not None:
            query = query.where(GradeModifier.kind == kind)
        return list(self.session.scalars(query.order_by(GradeModifier.kind, GradeModifier.label)))

    # -- certification ----------------------------------------------------

    def create_grading_company(self, code: str, name: str, **kwargs: Any) -> GradingCompany:
        company = GradingCompany(code=code, name=name, **kwargs)
        self.session.add(company)
        self.session.flush()
        return company

    def add_certification(
        self,
        specimen: Specimen,
        company: GradingCompany,
        *,
        cert_number: str | None = None,
        grade: SpecimenGrade | None = None,
        status: str = "current",
        rank: int | None = None,
        graded_on: date | None = None,
        holder_type: str | None = None,
        supersedes: Certification | None = None,
        **kwargs: Any,
    ) -> Certification:
        """Record a certification.

        Several may be ``current`` at once — a grading company's slab plus a separate
        endorsement — so this does not disturb other current certifications. A duplicate
        certification number is reported as a warning, never refused.
        """
        if cert_number:
            duplicate = self.session.scalar(
                select(Certification).where(
                    Certification.grading_company_id == company.id,
                    Certification.cert_number == cert_number,
                )
            )
            if duplicate is not None:
                self.warn(
                    "duplicate_cert_number",
                    f"{company.code} {cert_number} is already recorded on another coin",
                    specimen_id=specimen.id,
                )

        # Only a still-current certification becomes 'superseded'. If the user already recorded
        # *how* it ended — cracked out, crossed over — that is more specific and must stand.
        if supersedes is not None and supersedes.status == "current":
            supersedes.status = "superseded"

        certification = Certification(
            specimen_id=specimen.id,
            grading_company_id=company.id,
            cert_number=cert_number,
            specimen_grade_id=grade.id if grade else None,
            status=status,
            rank=rank if rank is not None else self._next_rank(Certification, specimen),
            graded_on=graded_on,
            holder_type=holder_type,
            supersedes_id=supersedes.id if supersedes else None,
            **kwargs,
        )
        self.session.add(certification)
        self.session.flush()
        return certification

    def primary_certification(self, specimen: Specimen) -> Certification | None:
        """The certification a single-value column shows: the one ranked first."""
        return self.session.scalars(
            select(Certification)
            .where(Certification.specimen_id == specimen.id, Certification.status == "current")
            .order_by(Certification.rank, Certification.id)
        ).first()

    def primary_reference(self, specimen: Specimen) -> CatalogReference | None:
        return self.session.scalars(
            select(CatalogReference)
            .where(CatalogReference.specimen_id == specimen.id)
            .order_by(CatalogReference.rank, CatalogReference.id)
        ).first()

    def attach_sticker(
        self,
        certification: Certification,
        grade: SpecimenGrade,
        modifier: GradeModifier | str,
        detail: str | None = None,
    ) -> SpecimenGradeModifier:
        """Record a sticker as issued by a certification.

        A sticker is a separate opinion about a grade rather than part of it, and it comes from
        its own company — CAC stickering a coin somebody else graded. Tying the instance to that
        company's certification is what keeps the two facts connected.
        """
        resolved, _ = self._resolve_modifier(modifier)
        link = self.session.scalar(
            select(SpecimenGradeModifier).where(
                SpecimenGradeModifier.specimen_grade_id == grade.id,
                SpecimenGradeModifier.grade_modifier_id == resolved.id,
            )
        )
        if link is None:
            link = SpecimenGradeModifier(
                specimen_grade_id=grade.id,
                grade_modifier_id=resolved.id,
                sort_order=len(grade.modifier_links),
            )
            self.session.add(link)
        link.detail = detail if detail is not None else link.detail
        link.certification_id = certification.id
        self.session.flush()
        self.refresh_grade_text(grade)
        return link

    def stickers_for(self, certification: Certification) -> list[SpecimenGradeModifier]:
        return list(
            self.session.scalars(
                select(SpecimenGradeModifier).where(
                    SpecimenGradeModifier.certification_id == certification.id
                )
            )
        )

    def certification_history(self, specimen: Specimen) -> list[Certification]:
        """Every certification for a coin, oldest first — the crack-out and regrade trail."""
        return list(
            self.session.scalars(
                select(Certification)
                .where(Certification.specimen_id == specimen.id)
                .order_by(
                    Certification.graded_on.is_(None),
                    Certification.graded_on,
                    Certification.id,
                )
            )
        )

    def current_certifications(self, specimen: Specimen) -> list[Certification]:
        """Every certification still standing, in the user's order of precedence.

        Ordered explicitly: without it SQLite decided what a certifications column listed first,
        and the answer could change between runs.
        """
        return list(
            self.session.scalars(
                select(Certification)
                .where(
                    Certification.specimen_id == specimen.id,
                    Certification.status == "current",
                )
                .order_by(Certification.rank, Certification.id)
            )
        )

    # -- external links ---------------------------------------------------

    def add_link(
        self,
        specimen: Specimen,
        url: str,
        *,
        kind: str = "other",
        label: str | None = None,
        reference: str | None = None,
        sort_order: int = 0,
    ) -> ExternalLink:
        link = ExternalLink(
            specimen_id=specimen.id,
            url=url,
            kind=kind,
            label=label,
            reference=reference,
            sort_order=sort_order,
        )
        self.session.add(link)
        self.session.flush()
        return link

    # -- history ledger ---------------------------------------------------

    def add_event(
        self,
        specimen: Specimen,
        event_type: str,
        *,
        occurred_on: date | None = None,
        amount: Any = None,
        fees: Any = None,
        shipping: Any = None,
        **kwargs: Any,
    ) -> SpecimenEvent:
        """Append a ledger entry. Amounts accept text and are stored as minor units."""
        decimals = self.currency_decimals

        def to_minor(value: Any) -> int | None:
            if value is None:
                return None
            if isinstance(value, int):
                return value
            from .fields.units import parse_money

            return parse_money(value, decimals)

        event = SpecimenEvent(
            specimen_id=specimen.id,
            event_type=event_type,
            occurred_on=occurred_on,
            amount_minor=to_minor(amount),
            fees_minor=to_minor(fees),
            shipping_minor=to_minor(shipping),
            **kwargs,
        )
        self.session.add(event)
        self.session.flush()
        self.session.refresh(event)  # net_minor is computed by the database
        self._project_status(specimen)
        return event

    def void_event(
        self, event: SpecimenEvent, reason: str, *, replacement: SpecimenEvent | None = None
    ) -> SpecimenEvent:
        """Void a ledger entry instead of editing it.

        The ledger is append-only, so a mistake is corrected by voiding and re-adding. That
        is what makes the financial history of a long-held collection trustworthy: it cannot
        be quietly rewritten, and every correction remains visible as one.
        """
        event.is_void = 1
        event.void_reason = reason
        event.voided_at = utcnow()
        if replacement is not None:
            replacement.corrects_event_id = event.id
        self.session.flush()
        self._project_status(self.session.get(Specimen, event.specimen_id))
        return event

    def _project_status(self, specimen: Specimen | None) -> None:
        """Recompute ``specimen.status`` from the ledger, which is the source of truth."""
        if specimen is None:
            return
        latest = self.session.scalars(
            select(SpecimenEvent)
            .where(SpecimenEvent.specimen_id == specimen.id, SpecimenEvent.is_void == 0)
            .order_by(
                SpecimenEvent.occurred_on.is_(None), SpecimenEvent.occurred_on, SpecimenEvent.id
            )
        ).all()
        status = "owned"
        for event in latest:
            if event.event_type in ("sold",):
                status = "sold"
            elif event.event_type in ("traded_out",):
                status = "traded"
            elif event.event_type in ("gifted_out",):
                status = "gifted"
            elif event.event_type in ("lost",):
                status = "lost"
            elif event.event_type in ("stolen",):
                status = "stolen"
            elif event.event_type in ("acquired", "received", "traded_in", "gifted_in", "found"):
                status = "owned"
            elif event.event_type == "ordered":
                status = "ordered"
            elif event.event_type == "loaned":
                status = "on_loan"
        specimen.status = status
        self.session.flush()

    def events_for(self, specimen: Specimen, *, include_void: bool = False) -> list[SpecimenEvent]:
        query = select(SpecimenEvent).where(SpecimenEvent.specimen_id == specimen.id)
        if not include_void:
            query = query.where(SpecimenEvent.is_void == 0)
        return list(
            self.session.scalars(
                query.order_by(
                    SpecimenEvent.occurred_on.is_(None),
                    SpecimenEvent.occurred_on,
                    SpecimenEvent.id,
                )
            )
        )

    def cost_basis(self, specimen: Specimen) -> int | None:
        """What the coin cost, in minor units, including fees and postage."""
        return self.session.scalar(
            select(func.sum(SpecimenEvent.net_minor)).where(
                SpecimenEvent.specimen_id == specimen.id,
                SpecimenEvent.is_void == 0,
                SpecimenEvent.event_type.in_(("acquired", "traded_in")),
            )
        )

    def proceeds(self, specimen: Specimen) -> int | None:
        """What the coin realised, in minor units, net of fees and postage."""
        return self.session.scalar(
            select(func.sum(SpecimenEvent.net_minor)).where(
                SpecimenEvent.specimen_id == specimen.id,
                SpecimenEvent.is_void == 0,
                SpecimenEvent.event_type.in_(("sold", "traded_out")),
            )
        )

    def realised_profit(self, specimen: Specimen) -> int | None:
        """Proceeds minus cost, or ``None`` while the coin is still held."""
        earned = self.proceeds(specimen)
        if earned is None:
            return None
        return earned - (self.cost_basis(specimen) or 0)

    # -- feature bindings -------------------------------------------------

    def set_binding(
        self,
        feature: str,
        purpose: str,
        *,
        field: FieldDefinition | None = None,
        catalog: Catalog | None = None,
        constant: Any = None,
        subcollection: Subcollection | None = None,
    ) -> FeatureBinding:
        """Tell a feature which field, catalogue or constant to use.

        Replaces the semantic-role idea: nothing is inferred from a field's name or type.
        """
        if field is not None:
            target_kind = "field"
        elif catalog is not None:
            target_kind = "catalogue"
        elif constant is not None:
            target_kind = "constant"
        else:
            target_kind = "none"

        existing = self.session.scalar(
            select(FeatureBinding).where(
                FeatureBinding.feature == feature,
                FeatureBinding.purpose == purpose,
                FeatureBinding.subcollection_id.is_(None)
                if subcollection is None
                else FeatureBinding.subcollection_id == subcollection.id,
            )
        )
        binding = existing or FeatureBinding(
            feature=feature,
            purpose=purpose,
            subcollection_id=subcollection.id if subcollection else None,
        )
        binding.target_kind = target_kind
        binding.field_definition_id = field.id if field else None
        binding.catalog_id = catalog.id if catalog else None
        binding.constant_json = json.dumps(constant) if constant is not None else None
        if existing is None:
            self.session.add(binding)
        self.session.flush()
        return binding

    def resolve_binding(
        self, feature: str, purpose: str, *, subcollection: Subcollection | None = None
    ) -> FeatureBinding:
        """Find the binding for a feature, preferring a subcollection override.

        Raises :class:`~numis.errors.BindingNotSet` when unset, carrying enough detail for
        the interface to offer to fix it rather than presenting a failure.
        """
        if subcollection is not None:
            specific = self.session.scalar(
                select(FeatureBinding).where(
                    FeatureBinding.feature == feature,
                    FeatureBinding.purpose == purpose,
                    FeatureBinding.subcollection_id == subcollection.id,
                )
            )
            if specific is not None and specific.target_kind != "none":
                return specific

        general = self.session.scalar(
            select(FeatureBinding).where(
                FeatureBinding.feature == feature,
                FeatureBinding.purpose == purpose,
                FeatureBinding.subcollection_id.is_(None),
            )
        )
        if general is None or general.target_kind == "none":
            raise BindingNotSet(feature, purpose)
        return general

    def bound_value(
        self, specimen: Specimen, feature: str, purpose: str
    ) -> Any:
        """The value a feature should use for a specimen, following its binding."""
        subcollection = self.session.get(Subcollection, specimen.subcollection_id)
        binding = self.resolve_binding(feature, purpose, subcollection=subcollection)
        if binding.target_kind == "constant":
            return json.loads(binding.constant_json or "null")
        if binding.target_kind == "field" and binding.field_definition_id:
            field = self.session.get(FieldDefinition, binding.field_definition_id)
            row = self.get_value(specimen, field)
            if row is None:
                return None
            return getattr(row, "value", None)
        return None

    # -- tags -------------------------------------------------------------

    def create_tag(self, name: str, *, parent: Tag | None = None, colour: str | None = None) -> Tag:
        tag = Tag(name=name, parent_id=parent.id if parent else None, colour=colour)
        self.session.add(tag)
        self.session.flush()
        return tag

    def tag_specimen(self, specimen: Specimen, tag: Tag) -> None:
        from .models import SpecimenTag

        exists = self.session.get(SpecimenTag, {"specimen_id": specimen.id, "tag_id": tag.id})
        if exists is None:
            self.session.add(SpecimenTag(specimen_id=specimen.id, tag_id=tag.id))
            self.session.flush()

    # -- saved views ------------------------------------------------------

    def save_view(
        self,
        name: str,
        *,
        subcollection: Subcollection | None = None,
        columns: Sequence[str] = (),
        sort: Sequence[dict[str, Any]] = (),
        filters: dict[str, Any] | None = None,
        group_by: str | None = None,
    ) -> SavedView:
        view = SavedView(
            name=name,
            subcollection_id=subcollection.id if subcollection else None,
            columns_json=json.dumps(list(columns)),
            sort_json=json.dumps(list(sort)),
            filter_json=json.dumps(filters or {}),
            group_by=group_by,
        )
        self.session.add(view)
        self.session.flush()
        return view

    # -- search -----------------------------------------------------------

    def reindex(self, specimen: Specimen) -> SpecimenSearch:
        """Rebuild the searchable text for one specimen."""
        titles = [specimen.display_name or "", specimen.inventory_code or ""]
        texts: list[str] = []
        notes: list[str] = []

        for model in (VALUE_MODELS["text"],):
            rows = self.session.execute(
                select(model, FieldDefinition.data_type)
                .join(FieldDefinition, FieldDefinition.id == model.field_definition_id)
                .where(model.specimen_id == specimen.id)
            )
            for row, data_type in rows:
                (notes if data_type == "long_text" else texts).append(row.value)

        for row in self.session.scalars(
            select(FieldValueDate).where(FieldValueDate.specimen_id == specimen.id)
        ):
            texts.append(row.display)

        catalogue_bits: list[str] = []
        for reference in self.references_for(specimen):
            catalog = self.session.get(Catalog, reference.catalog_id)
            code = catalog.code if catalog else ""
            catalogue_bits += [f"{code} {reference.number_raw}", f"{code}{reference.number_norm}"]

        everything = " ".join([*titles, *texts, *notes])
        record = self.session.get(SpecimenSearch, specimen.id)
        if record is None:
            record = SpecimenSearch(specimen_id=specimen.id)
            self.session.add(record)
        record.title_blob = " ".join(part for part in titles if part)
        record.text_blob = " ".join(part for part in texts if part)
        record.note_blob = " ".join(part for part in notes if part)
        record.catalog_blob = " ".join(catalogue_bits)
        record.cjk_blob = search.segment_cjk(everything)
        record.rebuilt_at = utcnow()
        self.session.flush()
        return record

    def reindex_all(self) -> int:
        count = 0
        for specimen in self.session.scalars(select(Specimen)):
            self.reindex(specimen)
            count += 1
        return count

    def search(self, term: str, *, subcollection: Subcollection | None = None) -> list[Specimen]:
        """Full-text search. CJK terms are routed to the segmented index automatically."""
        query = search.build_query(term)
        if not query:
            return []
        rows = self.session.execute(
            text("SELECT rowid FROM specimen_fts WHERE specimen_fts MATCH :q"), {"q": query}
        )
        ids = [row[0] for row in rows]
        if not ids:
            return []
        statement = self.live_specimens(subcollection).where(Specimen.id.in_(ids))
        return list(self.session.scalars(statement))


def _template_keys(template: str) -> list[str]:
    import re

    return re.findall(r"\{([a-zA-Z0-9_]+)\}", template)
