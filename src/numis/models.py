"""SQLAlchemy models for the base schema.

These must stay equivalent to ``docs/design/schema/base-v1.sql``, which is the normative
definition. ``tests/test_schema_parity.py`` compares the two and fails if they diverge.

Structural notes carried over from the design documents:

* Uniqueness over a nullable column always uses a *partial* index, because SQLite treats
  NULLs as distinct in unique indexes and would silently permit duplicates.
* ``scope_key`` generated columns exist so "one per library, or one per subcollection"
  uniqueness works even when ``subcollection_id`` is NULL.
* Field values attach to a specimen only. There is no coin-type layer.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Computed,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column, relationship

from . import constants as C
from .sqltypes import DateIso, UtcIso, utcnow


def new_uuid() -> str:
    return str(_uuid.uuid4())


def _in(column: str, allowed: tuple[str, ...], *, nullable: bool = False) -> CheckConstraint:
    """CHECK that a column is one of ``allowed`` (optionally also NULL)."""
    values = ", ".join(f"'{v}'" for v in allowed)
    clause = f"{column} IN ({values})"
    if nullable:
        clause = f"{column} IS NULL OR {clause}"
    return CheckConstraint(clause, name=f"ck_{column}")


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UtcIso, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UtcIso, nullable=False, default=utcnow, onupdate=utcnow
    )


class UuidMixin:
    uuid: Mapped[str] = mapped_column(String, nullable=False, unique=True, default=new_uuid)


# ---------------------------------------------------------------------------
# Library and subcollections
# ---------------------------------------------------------------------------


class LibraryMeta(TimestampMixin, Base):
    __tablename__ = "library_meta"
    __table_args__ = (CheckConstraint("id = 1", name="ck_single_row"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    library_uuid: Mapped[str] = mapped_column(String, nullable=False, unique=True, default=new_uuid)
    schema_version: Mapped[str] = mapped_column(String, nullable=False)
    app_version_created: Mapped[str] = mapped_column(String, nullable=False, default="")
    app_version_last_opened: Mapped[str | None] = mapped_column(String)
    currency_symbol: Mapped[str] = mapped_column(String, nullable=False, default="$")
    currency_code: Mapped[str] = mapped_column(String, nullable=False, default="USD")
    currency_decimals: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    length_display_unit: Mapped[str] = mapped_column(String, nullable=False, default="mm")
    mass_display_unit: Mapped[str] = mapped_column(String, nullable=False, default="g")
    settings_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class Subcollection(UuidMixin, TimestampMixin, Base):
    __tablename__ = "subcollection"
    __table_args__ = (Index("ix_subcollection_order", "sort_order", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    colour: Mapped[str | None] = mapped_column(String)
    naming_template: Mapped[str] = mapped_column(String, nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_archived: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)

    blocks: Mapped[list[SubcollectionBlock]] = relationship(
        back_populates="subcollection", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# Field system
# ---------------------------------------------------------------------------


class FieldGroup(UuidMixin, TimestampMixin, Base):
    __tablename__ = "field_group"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    collapsed_default: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)


class FieldDefinition(UuidMixin, TimestampMixin, Base):
    """A column the user defined. Library-wide; subcollections opt in via SubcollectionBlock.

    ``key`` is immutable once created because bindings, presets and label layouts reference
    it. ``label`` is freely renameable.
    """

    __tablename__ = "field_definition"
    __table_args__ = (
        _in("kind", C.FIELD_KINDS),
        Index("ix_field_active", "is_archived", "key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False, default="value")
    data_type: Mapped[str] = mapped_column(String, nullable=False)
    config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    help_text: Mapped[str | None] = mapped_column(Text)
    is_multi: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    is_archived: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    default_value_json: Mapped[str | None] = mapped_column(Text)
    origin_preset: Mapped[str | None] = mapped_column(String)


class SubcollectionBlock(UuidMixin, TimestampMixin, Base):
    """Which fields and special blocks a subcollection shows, and how they are labelled.

    A row with ``block_kind='field'`` places a field; other kinds place a special system
    (catalogues, grades, certifications, links, history) in the same layout ordering.
    """

    __tablename__ = "subcollection_block"
    __table_args__ = (
        _in("block_kind", C.BLOCK_KINDS),
        CheckConstraint(
            "(block_kind = 'field') = (field_definition_id IS NOT NULL)",
            name="ck_field_block_has_field",
        ),
        Index(
            "ux_block_field",
            "subcollection_id",
            "field_definition_id",
            unique=True,
            sqlite_where=text("field_definition_id IS NOT NULL"),
        ),
        Index(
            "ux_block_special",
            "subcollection_id",
            "block_kind",
            unique=True,
            sqlite_where=text("field_definition_id IS NULL"),
        ),
        Index("ix_block_order", "subcollection_id", "sort_order"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subcollection_id: Mapped[int] = mapped_column(
        ForeignKey("subcollection.id", ondelete="CASCADE"), nullable=False
    )
    block_kind: Mapped[str] = mapped_column(String, nullable=False)
    field_definition_id: Mapped[int | None] = mapped_column(
        ForeignKey("field_definition.id", ondelete="CASCADE")
    )
    display_label: Mapped[str | None] = mapped_column(String)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("field_group.id", ondelete="SET NULL"))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    show_in_table: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    is_required: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    subcollection: Mapped[Subcollection] = relationship(back_populates="blocks")
    field: Mapped[FieldDefinition | None] = relationship()

    @property
    def label(self) -> str | None:
        """The label to show here: the override if set, else the field's canonical label."""
        if self.display_label:
            return self.display_label
        return self.field.label if self.field else None


# ---------------------------------------------------------------------------
# Specimens
# ---------------------------------------------------------------------------


class Specimen(UuidMixin, TimestampMixin, Base):
    """One physical coin. There is deliberately no quantity column: a lot of 47 coins is
    47 rows, created in one action by bulk add, so totals are always a plain row count.
    """

    __tablename__ = "specimen"
    __table_args__ = (
        _in("status", C.SPECIMEN_STATUSES),
        Index(
            "ix_specimen_sub",
            "subcollection_id",
            "status",
            sqlite_where=text("deleted_at IS NULL"),
        ),
        Index("ix_specimen_name", "display_name"),
        Index(
            "ux_specimen_inv",
            "inventory_code",
            unique=True,
            sqlite_where=text("inventory_code IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subcollection_id: Mapped[int] = mapped_column(
        ForeignKey("subcollection.id", ondelete="RESTRICT"), nullable=False
    )
    display_name: Mapped[str] = mapped_column(String, nullable=False, default="")
    #: Whether the name was typed by hand. Automatic names follow the naming template.
    display_name_manual: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    inventory_code: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, nullable=False, default="owned")
    is_favourite: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    deleted_at: Mapped[datetime | None] = mapped_column(UtcIso)

    subcollection: Mapped[Subcollection] = relationship()
    grades: Mapped[list[SpecimenGrade]] = relationship(
        back_populates="specimen", cascade="all, delete-orphan"
    )
    certifications: Mapped[list[Certification]] = relationship(
        back_populates="specimen", cascade="all, delete-orphan"
    )
    catalog_references: Mapped[list[CatalogReference]] = relationship(
        back_populates="specimen", cascade="all, delete-orphan"
    )
    links: Mapped[list[ExternalLink]] = relationship(
        back_populates="specimen", cascade="all, delete-orphan", order_by="ExternalLink.sort_order"
    )
    events: Mapped[list[SpecimenEvent]] = relationship(
        back_populates="specimen", cascade="all, delete-orphan", order_by="SpecimenEvent.id"
    )


# ---------------------------------------------------------------------------
# Field values
# ---------------------------------------------------------------------------


class FieldValueMixin:
    """Shared columns for every typed value table."""

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    @declared_attr
    def field_definition_id(cls) -> Mapped[int]:  # noqa: N805
        return mapped_column(
            ForeignKey("field_definition.id", ondelete="CASCADE"), nullable=False
        )

    @declared_attr
    def specimen_id(cls) -> Mapped[int]:  # noqa: N805
        return mapped_column(ForeignKey("specimen.id", ondelete="CASCADE"), nullable=False)

    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


def _value_indexes(prefix: str, table: str, *extra: Index) -> tuple[Index, ...]:
    return (
        Index(f"ux_{prefix}", "field_definition_id", "specimen_id", "seq", unique=True),
        Index(f"ix_{prefix}_spec", "specimen_id"),
        *extra,
    )


class FieldValueText(FieldValueMixin, Base):
    __tablename__ = "field_value_text"
    __table_args__ = (
        _in("sort_source", C.SORT_SOURCES),
        *_value_indexes(
            "fvtext",
            "field_value_text",
            Index("ix_fvtext_val", "field_definition_id", "value"),
            Index("ix_fvtext_sort", "field_definition_id", "sort_value"),
        ),
    )

    value: Mapped[str] = mapped_column(Text, nullable=False)
    #: Optional numeric ordering key. The app proposes it, the user may override it.
    sort_value: Mapped[float | None] = mapped_column()
    sort_source: Mapped[str] = mapped_column(String, nullable=False, default="none")
    needs_review: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)


class FieldValueNumber(FieldValueMixin, Base):
    __tablename__ = "field_value_number"
    __table_args__ = _value_indexes(
        "fvnum",
        "field_value_number",
        Index("ix_fvnum_val", "field_definition_id", "value"),
    )

    #: Canonical unit for the field's data type: grams, millimetres, per mille, degrees.
    value: Mapped[float] = mapped_column(nullable=False)
    entered_as: Mapped[str | None] = mapped_column(String)
    is_approximate: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)


class FieldValueMoney(FieldValueMixin, Base):
    __tablename__ = "field_value_money"
    __table_args__ = _value_indexes(
        "fvmoney",
        "field_value_money",
        Index("ix_fvmoney_val", "field_definition_id", "amount_minor"),
    )

    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    as_of: Mapped[date | None] = mapped_column(DateIso)


class FieldValueDate(FieldValueMixin, Base):
    """A date that describes a coin: possibly a range, an approximation or another era."""

    __tablename__ = "field_value_date"
    __table_args__ = (
        _in("precision", C.DATE_PRECISIONS),
        _in("calendar", C.CALENDARS),
        _in("sort_source", C.SORT_SOURCES),
        *_value_indexes(
            "fvdate",
            "field_value_date",
            Index("ix_fvdate_sort", "field_definition_id", "sort_value"),
            Index("ix_fvdate_span", "field_definition_id", "year_start", "year_end"),
        ),
    )

    #: Exactly as the user expressed it; never regenerated over their input.
    display: Mapped[str] = mapped_column(String, nullable=False)
    year_start: Mapped[int | None] = mapped_column(Integer)
    year_end: Mapped[int | None] = mapped_column(Integer)
    month_start: Mapped[int | None] = mapped_column(Integer)
    day_start: Mapped[int | None] = mapped_column(Integer)
    month_end: Mapped[int | None] = mapped_column(Integer)
    day_end: Mapped[int | None] = mapped_column(Integer)
    precision: Mapped[str] = mapped_column(String, nullable=False, default="exact_year")
    calendar: Mapped[str] = mapped_column(String, nullable=False, default="gregorian")
    era_label: Mapped[str | None] = mapped_column(String)
    sort_value: Mapped[float | None] = mapped_column()
    sort_source: Mapped[str] = mapped_column(String, nullable=False, default="none")
    needs_review: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)


class FieldValueBool(FieldValueMixin, Base):
    __tablename__ = "field_value_bool"
    __table_args__ = (
        CheckConstraint("value IN (0,1)", name="ck_bool_value"),
        *_value_indexes("fvbool", "field_value_bool"),
    )

    value: Mapped[bool] = mapped_column(Integer, nullable=False)


class FieldValueJson(FieldValueMixin, Base):
    """Display-only escape hatch; never sorted or filtered."""

    __tablename__ = "field_value_json"
    __table_args__ = _value_indexes("fvjson", "field_value_json")

    value: Mapped[str] = mapped_column(Text, nullable=False)


#: Maps a storage name to its model, for generic value handling.
VALUE_MODELS = {
    "text": FieldValueText,
    "number": FieldValueNumber,
    "money": FieldValueMoney,
    "date": FieldValueDate,
    "bool": FieldValueBool,
    "json": FieldValueJson,
}


# ---------------------------------------------------------------------------
# Catalogues
# ---------------------------------------------------------------------------


class Catalog(UuidMixin, TimestampMixin, Base):
    __tablename__ = "catalog"
    __table_args__ = (
        _in("sort_strategy", C.CATALOG_SORT_STRATEGIES),
        _in("letter_prefix_order", C.LETTER_PREFIX_ORDERS),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    publisher: Mapped[str | None] = mapped_column(String)
    edition: Mapped[str | None] = mapped_column(String)
    year: Mapped[int | None] = mapped_column(Integer)
    scope: Mapped[str | None] = mapped_column(String)
    url_template: Mapped[str | None] = mapped_column(String)
    number_pattern: Mapped[str | None] = mapped_column(String)
    sort_strategy: Mapped[str] = mapped_column(String, nullable=False, default="prefix_aware")
    letter_prefix_order: Mapped[str] = mapped_column(String, nullable=False, default="after")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_archived: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    notes: Mapped[str | None] = mapped_column(Text)


class CatalogReference(UuidMixin, TimestampMixin, Base):
    __tablename__ = "catalog_reference"
    __table_args__ = (
        _in("certainty", C.CATALOG_CERTAINTIES),
        Index("ux_catref", "specimen_id", "catalog_id", "number_norm", unique=True),
        Index("ix_catref_sort", "catalog_id", "sort_segments"),
        Index("ix_catref_spec", "specimen_id", "rank"),
        Index("ix_catref_norm", "catalog_id", "number_norm"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    catalog_id: Mapped[int] = mapped_column(
        ForeignKey("catalog.id", ondelete="RESTRICT"), nullable=False
    )
    specimen_id: Mapped[int] = mapped_column(
        ForeignKey("specimen.id", ondelete="CASCADE"), nullable=False
    )
    number_raw: Mapped[str] = mapped_column(String, nullable=False)
    number_norm: Mapped[str] = mapped_column(String, nullable=False)
    sort_segments: Mapped[str] = mapped_column(String, nullable=False)
    segments_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    qualifier: Mapped[str | None] = mapped_column(String)
    certainty: Mapped[str] = mapped_column(String, nullable=False, default="certain")
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    url: Mapped[str | None] = mapped_column(String)
    notes: Mapped[str | None] = mapped_column(Text)

    catalog: Mapped[Catalog] = relationship()
    specimen: Mapped[Specimen] = relationship(back_populates="catalog_references")


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------


class GradeScale(UuidMixin, TimestampMixin, Base):
    """A grading standard the user defined. The app ships with none."""

    __tablename__ = "grade_scale"
    __table_args__ = (_in("kind", C.GRADE_SCALE_KINDS),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False, default="ordinal")
    notes: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_archived: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)

    levels: Mapped[list[GradeLevel]] = relationship(
        back_populates="scale", cascade="all, delete-orphan", order_by="GradeLevel.normalised"
    )


class GradeLevel(UuidMixin, TimestampMixin, Base):
    """One value within a scale, positioned on the shared comparison axis."""

    __tablename__ = "grade_level"
    __table_args__ = (
        Index("ux_grade_level", "grade_scale_id", "label", unique=True),
        Index("ix_grade_level_n", "normalised"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    grade_scale_id: Mapped[int] = mapped_column(
        ForeignKey("grade_scale.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(String, nullable=False)
    #: Pipe-separated alternative spellings, e.g. ``MS-63|MS 63|Mint State 63``.
    aliases: Mapped[str | None] = mapped_column(String)
    numeric_value: Mapped[float | None] = mapped_column()
    normalised: Mapped[float] = mapped_column(nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    scale: Mapped[GradeScale] = relationship(back_populates="levels")


class GradeModifier(UuidMixin, TimestampMixin, Base):
    """Something attached to a grade: Details, a sticker, a plus, a star, a strike or colour.

    ``normalised_delta`` is what keeps 'AU Details' sorted immediately below 'AU' rather
    than banished to the bottom of the collection.
    """

    __tablename__ = "grade_modifier"
    __table_args__ = (_in("kind", C.GRADE_MODIFIER_KINDS),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    #: Full name, e.g. ``Full Bands``.
    label: Mapped[str] = mapped_column(String, nullable=False)
    #: Short form used in columns, e.g. ``FB``. Falls back to the label.
    abbreviation: Mapped[str | None] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    #: Who issues it, for stickers: CAC, CACG, WINGS, CNAS.
    issuer: Mapped[str | None] = mapped_column(String)
    #: ``+`` and ``*`` read as ``MS63+``, with no space.
    attach_without_space: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    normalised_delta: Mapped[float] = mapped_column(nullable=False, default=0.0)
    #: Where this one appears among a grade's modifiers. Belongs to the definition rather than
    #: to a coin, so two coins carrying the same modifiers can never read them differently.
    #: 0 means "wherever its kind falls"; 1 and up are placed ahead of that.
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    colour: Mapped[str | None] = mapped_column(String)
    notes: Mapped[str | None] = mapped_column(Text)

    @property
    def short(self) -> str:
        """What a column shows by default: the short form, falling back to the full name."""
        return self.abbreviation or self.label

    def reads_as(self, *, full_name: bool = False, with_issuer: bool = False) -> str:
        """The modifier's own name, before anything a particular coin adds to it.

        A sticker's issuer is *not* part of this by default. Recording that CAC issued a sticker
        is worth doing, but a column showing only ``CAC`` when the user named the modifier
        ``CAC Gold`` throws away the part they chose to type.
        """
        text = self.label if full_name else self.short
        if with_issuer and self.issuer and not text.lower().startswith(self.issuer.lower()):
            return f"{self.issuer} {text}"
        return text


class SpecimenGradeModifier(Base):
    """One modifier on one grade.

    Has its own key because a certification can point at a particular instance: a CAC sticker
    is recorded as issued by CAC's certification, not as an anonymous property of the grade.
    """

    __tablename__ = "specimen_grade_modifier"
    __table_args__ = (
        Index("ux_sgm", "specimen_grade_id", "grade_modifier_id", unique=True),
        Index("ix_sgm_mod", "grade_modifier_id"),
        Index("ix_sgm_cert", "certification_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    specimen_grade_id: Mapped[int] = mapped_column(
        ForeignKey("specimen_grade.id", ondelete="CASCADE"), nullable=False
    )
    grade_modifier_id: Mapped[int] = mapped_column(
        ForeignKey("grade_modifier.id", ondelete="RESTRICT"), nullable=False
    )
    #: What this one says: ``Harshly Cleaned``, ``Gold``, ``Full Bands``, ``Brown``.
    detail: Mapped[str | None] = mapped_column(String)
    certification_id: Mapped[int | None] = mapped_column(
        ForeignKey("certification.id", ondelete="SET NULL")
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    modifier: Mapped[GradeModifier] = relationship()
    grade: Mapped[SpecimenGrade] = relationship(back_populates="modifier_links")
    certification: Mapped[Certification | None] = relationship(back_populates="sticker_links")


class SpecimenGrade(UuidMixin, TimestampMixin, Base):
    """A grade recorded against a coin, from any source.

    A coin may hold several. Which one is the headline grade is chosen by the user via
    ``rank``; it is never inferred from recency or from the source's authority.
    """

    __tablename__ = "specimen_grade"
    __table_args__ = (
        _in("source", C.GRADE_SOURCES),
        Index("ix_grade_norm", "normalised"),
        Index("ix_grade_spec", "specimen_id", "rank"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    specimen_id: Mapped[int] = mapped_column(
        ForeignKey("specimen.id", ondelete="CASCADE"), nullable=False
    )
    grade_scale_id: Mapped[int | None] = mapped_column(
        ForeignKey("grade_scale.id", ondelete="SET NULL")
    )
    grade_level_id: Mapped[int | None] = mapped_column(
        ForeignKey("grade_level.id", ondelete="SET NULL")
    )
    #: What the user typed for the grade itself, e.g. ``MS63``.
    grade_label: Mapped[str] = mapped_column(String, nullable=False, default="")
    #: What that grade counts as on its own, e.g. 63.
    base_value: Mapped[float | None] = mapped_column()
    #: Rendered display including modifiers, cached for the grid.
    raw_text: Mapped[str] = mapped_column(String, nullable=False)
    #: ``base_value`` plus every modifier's delta. Sorting compares this.
    normalised: Mapped[float | None] = mapped_column()
    detail_note: Mapped[str | None] = mapped_column(String)
    source: Mapped[str] = mapped_column(String, nullable=False, default="self")
    assigned_by: Mapped[str | None] = mapped_column(String)
    hide_assigned_by: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    assigned_on: Mapped[date | None] = mapped_column(DateIso)
    #: 1 is the grade shown in a single-value column; 2, 3 … sit behind it.
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    notes: Mapped[str | None] = mapped_column(Text)

    specimen: Mapped[Specimen] = relationship(back_populates="grades")
    scale: Mapped[GradeScale | None] = relationship()
    level: Mapped[GradeLevel | None] = relationship()
    #: The modifier instances, each with its own detail and issuing certification.
    modifier_links: Mapped[list[SpecimenGradeModifier]] = relationship(
        cascade="all, delete-orphan",
        order_by="SpecimenGradeModifier.sort_order",
        back_populates="grade",
    )

    @property
    def modifiers(self) -> list[GradeModifier]:
        """The modifier definitions, in display order."""
        return [link.modifier for link in self.modifier_links]


# ---------------------------------------------------------------------------
# Certification
# ---------------------------------------------------------------------------


class GradingCompany(UuidMixin, TimestampMixin, Base):
    __tablename__ = "grading_company"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    cert_url_template: Mapped[str | None] = mapped_column(String)
    default_scale_id: Mapped[int | None] = mapped_column(
        ForeignKey("grade_scale.id", ondelete="SET NULL")
    )
    specialism: Mapped[str | None] = mapped_column(String)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_archived: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    notes: Mapped[str | None] = mapped_column(Text)


class Certification(UuidMixin, TimestampMixin, Base):
    """A certification on a coin.

    Several may be ``current`` at once — a grading company's slab plus a separate
    endorsement sticker — so 'current' is not unique. ``rank`` decides which one a
    single-value column shows.
    """

    __tablename__ = "certification"
    __table_args__ = (
        _in("status", C.CERTIFICATION_STATUSES),
        Index("ix_cert_spec", "specimen_id", "status", "rank"),
        Index("ix_cert_number", "grading_company_id", "cert_number"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    specimen_id: Mapped[int] = mapped_column(
        ForeignKey("specimen.id", ondelete="CASCADE"), nullable=False
    )
    grading_company_id: Mapped[int] = mapped_column(
        ForeignKey("grading_company.id", ondelete="RESTRICT"), nullable=False
    )
    #: Nullable because some endorsements do not issue a number.
    cert_number: Mapped[str | None] = mapped_column(String)
    specimen_grade_id: Mapped[int | None] = mapped_column(
        ForeignKey("specimen_grade.id", ondelete="SET NULL")
    )
    holder_type: Mapped[str | None] = mapped_column(String)
    label_variety: Mapped[str | None] = mapped_column(String)
    graded_on: Mapped[date | None] = mapped_column(DateIso)
    status: Mapped[str] = mapped_column(String, nullable=False, default="current")
    supersedes_id: Mapped[int | None] = mapped_column(
        ForeignKey("certification.id", ondelete="SET NULL")
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    population_note: Mapped[str | None] = mapped_column(String)
    verification_url: Mapped[str | None] = mapped_column(String)
    verified_at: Mapped[datetime | None] = mapped_column(UtcIso)
    notes: Mapped[str | None] = mapped_column(Text)

    specimen: Mapped[Specimen] = relationship(back_populates="certifications")
    company: Mapped[GradingCompany] = relationship()
    grade: Mapped[SpecimenGrade | None] = relationship()
    #: Sticker instances this certification issued, e.g. CAC's green sticker.
    sticker_links: Mapped[list[SpecimenGradeModifier]] = relationship(
        back_populates="certification"
    )


# ---------------------------------------------------------------------------
# External links
# ---------------------------------------------------------------------------


class ExternalLink(UuidMixin, TimestampMixin, Base):
    """A link to this coin's record elsewhere: Zeno, a grading lookup, an auction lot."""

    __tablename__ = "external_link"
    __table_args__ = (
        _in("kind", C.LINK_KINDS),
        Index("ix_link_spec", "specimen_id", "rank", "sort_order"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    specimen_id: Mapped[int] = mapped_column(
        ForeignKey("specimen.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String, nullable=False, default="other")
    label: Mapped[str | None] = mapped_column(String)
    url: Mapped[str] = mapped_column(String, nullable=False)
    reference: Mapped[str | None] = mapped_column(String)
    notes: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    specimen: Mapped[Specimen] = relationship(back_populates="links")


# ---------------------------------------------------------------------------
# History ledger
# ---------------------------------------------------------------------------

_NET_MINOR = (
    "CASE WHEN event_type IN ('sold','traded_out','gifted_out') "
    "THEN COALESCE(amount_minor,0) - COALESCE(fees_minor,0) - COALESCE(shipping_minor,0) "
    "ELSE COALESCE(amount_minor,0) + COALESCE(fees_minor,0) + COALESCE(shipping_minor,0) END"
)


class SpecimenEvent(UuidMixin, Base):
    """An append-only ledger entry.

    Rows are never updated except to set ``is_void``. A mistake is corrected by voiding the
    original and inserting a replacement pointing back via ``corrects_event_id``, so the
    financial history cannot be quietly rewritten. There is no ``updated_at`` because
    nothing is updated.
    """

    __tablename__ = "specimen_event"
    __table_args__ = (
        _in("event_type", C.EVENT_TYPES),
        _in("occurred_precision", C.EVENT_PRECISIONS),
        _in("counterparty_kind", C.COUNTERPARTY_KINDS, nullable=True),
        Index("ix_event_spec", "specimen_id", "occurred_on"),
        Index("ix_event_type", "event_type", "occurred_on", sqlite_where=text("is_void = 0")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    specimen_id: Mapped[int] = mapped_column(
        ForeignKey("specimen.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    occurred_on: Mapped[date | None] = mapped_column(DateIso)
    occurred_precision: Mapped[str] = mapped_column(String, nullable=False, default="exact_day")
    amount_minor: Mapped[int | None] = mapped_column(Integer)
    fees_minor: Mapped[int | None] = mapped_column(Integer)
    shipping_minor: Mapped[int | None] = mapped_column(Integer)
    #: Cash that actually moved. Fees and postage add to a purchase and subtract from a
    #: sale; summing them in both directions would overstate every profit figure.
    net_minor: Mapped[int] = mapped_column(Integer, Computed(_NET_MINOR, persisted=True))
    counterparty: Mapped[str | None] = mapped_column(String)
    counterparty_kind: Mapped[str | None] = mapped_column(String)
    venue: Mapped[str | None] = mapped_column(String)
    lot_reference: Mapped[str | None] = mapped_column(String)
    invoice_reference: Mapped[str | None] = mapped_column(String)
    notes: Mapped[str | None] = mapped_column(Text)
    is_void: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    void_reason: Mapped[str | None] = mapped_column(Text)
    voided_at: Mapped[datetime | None] = mapped_column(UtcIso)
    corrects_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("specimen_event.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(UtcIso, nullable=False, default=utcnow)

    specimen: Mapped[Specimen] = relationship(back_populates="events")


# ---------------------------------------------------------------------------
# Feature bindings
# ---------------------------------------------------------------------------


class FeatureBinding(UuidMixin, TimestampMixin, Base):
    """The user's answer to "which field should this feature use?".

    Replaces the semantic-role idea from the first draft: nothing is inferred. A binding may
    also point at a fixed constant, so a print run needs no field to exist at all.
    """

    __tablename__ = "feature_binding"
    __table_args__ = (
        _in("target_kind", C.BINDING_TARGET_KINDS),
        Index("ux_binding", "feature", "purpose", "scope_key", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    feature: Mapped[str] = mapped_column(String, nullable=False)
    purpose: Mapped[str] = mapped_column(String, nullable=False)
    subcollection_id: Mapped[int | None] = mapped_column(
        ForeignKey("subcollection.id", ondelete="CASCADE")
    )
    #: Generated so "one per library" uniqueness works when subcollection_id is NULL,
    #: which a plain unique index would not enforce.
    scope_key: Mapped[int] = mapped_column(
        Integer, Computed("COALESCE(subcollection_id, 0)", persisted=True)
    )
    target_kind: Mapped[str] = mapped_column(String, nullable=False)
    field_definition_id: Mapped[int | None] = mapped_column(
        ForeignKey("field_definition.id", ondelete="SET NULL")
    )
    catalog_id: Mapped[int | None] = mapped_column(ForeignKey("catalog.id", ondelete="SET NULL"))
    constant_json: Mapped[str | None] = mapped_column(Text)
    config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    field: Mapped[FieldDefinition | None] = relationship()
    catalog: Mapped[Catalog | None] = relationship()


# ---------------------------------------------------------------------------
# Tags, views, search
# ---------------------------------------------------------------------------


class Tag(UuidMixin, TimestampMixin, Base):
    __tablename__ = "tag"
    __table_args__ = (
        # Expression index: COALESCE gives root tags (parent_id NULL) a real value to be
        # unique on, since SQLite treats NULLs as distinct in unique indexes.
        Index(
            "ux_tag_name",
            text("COALESCE(parent_id, 0)"),
            text("name COLLATE NOCASE"),
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("tag.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String, nullable=False)
    colour: Mapped[str | None] = mapped_column(String)
    notes: Mapped[str | None] = mapped_column(Text)


class SpecimenTag(Base):
    __tablename__ = "specimen_tag"
    __table_args__ = (
        Index("ix_specimen_tag", "tag_id"),
        {"sqlite_with_rowid": False},
    )

    specimen_id: Mapped[int] = mapped_column(
        ForeignKey("specimen.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(ForeignKey("tag.id", ondelete="CASCADE"), primary_key=True)


class SavedView(UuidMixin, TimestampMixin, Base):
    __tablename__ = "saved_view"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    subcollection_id: Mapped[int | None] = mapped_column(
        ForeignKey("subcollection.id", ondelete="CASCADE")
    )
    filter_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    sort_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    columns_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    group_by: Mapped[str | None] = mapped_column(String)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class SpecimenSearch(Base):
    """Materialised searchable text, one row per specimen.

    ``cjk_blob`` holds CJK content with every ideograph space-separated, which is what makes
    two-character terms such as 通寶 findable; see docs/design/01, Part 6.
    """

    __tablename__ = "specimen_search"

    specimen_id: Mapped[int] = mapped_column(
        ForeignKey("specimen.id", ondelete="CASCADE"), primary_key=True
    )
    title_blob: Mapped[str] = mapped_column(Text, nullable=False, default="")
    text_blob: Mapped[str] = mapped_column(Text, nullable=False, default="")
    catalog_blob: Mapped[str] = mapped_column(Text, nullable=False, default="")
    note_blob: Mapped[str] = mapped_column(Text, nullable=False, default="")
    cjk_blob: Mapped[str] = mapped_column(Text, nullable=False, default="")
    rebuilt_at: Mapped[datetime] = mapped_column(UtcIso, nullable=False, default=utcnow)


#: The FTS5 index and its synchronising triggers cannot be expressed as ORM models, so they
#: are created as raw DDL. An external-content FTS table does not update itself; these
#: triggers are the pattern SQLite documents for keeping one in step with its content table.
FTS_DDL: tuple[str, ...] = (
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS specimen_fts USING fts5(
      title_blob, text_blob, catalog_blob, note_blob, cjk_blob,
      content       = 'specimen_search',
      content_rowid = 'specimen_id',
      tokenize      = "unicode61 remove_diacritics 2"
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS specimen_search_ai AFTER INSERT ON specimen_search BEGIN
      INSERT INTO specimen_fts(rowid, title_blob, text_blob, catalog_blob, note_blob, cjk_blob)
      VALUES (new.specimen_id, new.title_blob, new.text_blob, new.catalog_blob,
              new.note_blob, new.cjk_blob);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS specimen_search_ad AFTER DELETE ON specimen_search BEGIN
      INSERT INTO specimen_fts(specimen_fts, rowid, title_blob, text_blob, catalog_blob,
                               note_blob, cjk_blob)
      VALUES ('delete', old.specimen_id, old.title_blob, old.text_blob, old.catalog_blob,
              old.note_blob, old.cjk_blob);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS specimen_search_au AFTER UPDATE ON specimen_search BEGIN
      INSERT INTO specimen_fts(specimen_fts, rowid, title_blob, text_blob, catalog_blob,
                               note_blob, cjk_blob)
      VALUES ('delete', old.specimen_id, old.title_blob, old.text_blob, old.catalog_blob,
              old.note_blob, old.cjk_blob);
      INSERT INTO specimen_fts(rowid, title_blob, text_blob, catalog_blob, note_blob, cjk_blob)
      VALUES (new.specimen_id, new.title_blob, new.text_blob, new.catalog_blob,
              new.note_blob, new.cjk_blob);
    END
    """,
)
