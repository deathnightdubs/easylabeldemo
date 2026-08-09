"""Grading: the numbers, and how a grade reads.

A grade is three things the user supplies — the label they want to see (``MS63``), what that
counts as on its own (63), and any modifiers — and one thing the application works out: the
comparable value, which is the base plus every modifier's effect. That calculated value is what
sorting uses, so grades from unrelated standards can be ordered together.

Nothing is looked up from a curated list of grades. Requiring one meant a shared registry the
user had to maintain, and typing a grade that already existed on a scale hit a uniqueness
constraint and took the application down with it.

Rendering lives here too, because "how does this grade read in a column" has more cases than it
first appears:

* a plus or a star attaches with no space:      ``MS63+``
* other modifiers are separated:                ``MS63 FB``
* several combine in a settled order:           ``MS63 FB BN``
* short forms or full names:                    ``MS63 FB`` or ``MS63 Full Bands``
* what a coin adds is optional:                 ``AU Details`` or ``AU Details — Harshly Cleaned``
* a sticker reads by the name it was given:     ``MS63 CAC Gold``, not ``MS63 CAC``

Two things are deliberately kept apart. A modifier's **own name** belongs to its definition and is
shared: ``FB``/``Full Bands``, ``Details``, ``CAC Gold``. What a **particular coin** adds to it is
recorded against that coin: ``Harshly Cleaned``, ``Gold``. That is what stops the user having to
define a separate modifier for every problem a coin might have.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .models import GradeModifier, SpecimenGrade, SpecimenGradeModifier

#: Modifier kinds meaning the coin has a problem rather than a better appearance.
PROBLEM_KINDS = ("detail",)

#: The order modifiers read in, so two coins never show the same set differently.
KIND_ORDER = ("qualifier", "strike", "colour", "contrast", "sticker", "detail")


@dataclass(frozen=True)
class GradeDisplay:
    """What to show for a grade, and how much of it."""

    modifiers: bool = True
    #: Add what each modifier says *on this coin*: ``Details — Harshly Cleaned``, ``CAC Gold``.
    #: Only has an effect where the coin actually records something; see :func:`render_modifier`.
    modifier_details: bool = False
    #: Full names instead of short forms: ``Full Bands`` rather than ``FB``, ``Red`` not ``RD``.
    modifier_full_names: bool = False
    #: Name the company that issued a sticker, when the modifier's own name does not already.
    sticker_issuer: bool = False
    scale: bool = False
    source: bool = False
    assigned_by: bool = False

    @classmethod
    def compact(cls) -> GradeDisplay:
        return cls()

    @classmethod
    def full(cls) -> GradeDisplay:
        return cls(
            modifiers=True,
            modifier_details=True,
            modifier_full_names=True,
            sticker_issuer=True,
            scale=True,
            source=True,
            assigned_by=True,
        )


def calculated_value(
    base_value: float | None, modifiers: Iterable[GradeModifier] = ()
) -> float | None:
    """The comparable value: the base plus every modifier's effect.

    A ``Details`` modifier carries a small negative effect, which is what keeps ``AU Details``
    immediately below ``AU`` instead of at the bottom of the collection. A sticker nudges a coin
    slightly up within its grade.
    """
    if base_value is None:
        return None
    return base_value + sum(modifier.normalised_delta for modifier in modifiers)


def _ordered(links: Sequence[SpecimenGradeModifier]) -> list[SpecimenGradeModifier]:
    """The order a grade's modifiers read in.

    ``display_order`` comes first so the user can decide the sequence, and it lives on the
    *definition* rather than on the coin: two coins carrying the same modifiers must never show
    them differently. Anything left at 0 falls back to :data:`KIND_ORDER`, which is the order
    these read in on a real slab.
    """

    def key(link: SpecimenGradeModifier) -> tuple[int, int, int, str]:
        modifier = link.modifier
        kind = modifier.kind if modifier else ""
        position = KIND_ORDER.index(kind) if kind in KIND_ORDER else len(KIND_ORDER)
        chosen = modifier.display_order if modifier else 0
        # 0 sorts last within the first element so an unordered modifier stays where its kind
        # puts it, while anything explicitly ordered is pulled in front.
        return (0 if chosen else 1, chosen, position, modifier.code if modifier else "")

    return sorted(sorted(links, key=lambda link: link.sort_order), key=key)


def order_pairs(
    pairs: Iterable[tuple[GradeModifier, str | None]],
) -> list[tuple[GradeModifier, str | None]]:
    """Put ``(modifier, detail)`` pairs into reading order, as :func:`_ordered` does for links."""

    def key(pair: tuple[GradeModifier, str | None]) -> tuple[int, int, int, str]:
        modifier = pair[0]
        position = (
            KIND_ORDER.index(modifier.kind)
            if modifier.kind in KIND_ORDER
            else len(KIND_ORDER)
        )
        chosen = modifier.display_order or 0
        return (0 if chosen else 1, chosen, position, modifier.code)

    return sorted(pairs, key=key)


def render_pair(
    modifier: GradeModifier,
    detail: str | None,
    *,
    with_detail: bool,
    full_names: bool = False,
    with_issuer: bool = False,
) -> str:
    """How one modifier reads, from the modifier and what a coin says about it.

    Separate from :func:`render_modifier` so a dialog can preview a grade that has not been
    saved, and so the preview cannot drift from the column.
    """
    text = modifier.reads_as(full_name=full_names, with_issuer=with_issuer)
    if not with_detail or not detail:
        return text
    # A modifier named 'CAC Gold' on a coin whose sticker says 'Gold' must not read 'CAC Gold
    # Gold'. Naming the variant in the modifier *and* recording it per coin is a reasonable
    # thing to do, and saying it twice is never what was meant.
    if detail.strip().casefold() in text.casefold():
        return text
    # A problem grade reads 'Details — Harshly Cleaned': the dash matters, because 'Details
    # Harshly Cleaned' looks like the name of a grade rather than a grade and its explanation.
    separator = " — " if modifier.kind == "detail" else " "
    return f"{text}{separator}{detail.strip()}"


def assemble(
    label: str,
    pairs: Iterable[tuple[GradeModifier, str | None]],
    display: GradeDisplay | None = None,
) -> str:
    """A grade label with its modifiers appended, in reading order."""
    display = display or GradeDisplay()
    text = label or ""
    if not display.modifiers:
        return text
    for modifier, detail in order_pairs(pairs):
        piece = render_pair(
            modifier,
            detail,
            with_detail=display.modifier_details,
            full_names=display.modifier_full_names,
            with_issuer=display.sticker_issuer,
        )
        if not piece:
            continue
        if modifier.attach_without_space:
            text += piece
        else:
            text = f"{text} {piece}" if text else piece
    return text


def render_modifier(
    link: SpecimenGradeModifier,
    *,
    with_detail: bool,
    full_names: bool = False,
    with_issuer: bool = False,
) -> str:
    """How one modifier reads on one coin.

    Two separate things are being combined, and keeping them separate is the point:

    * the **modifier's own name**, which the user chose — ``FB`` or ``Full Bands``, ``Details``,
      ``CAC Gold``;
    * what this **particular coin** adds to it, recorded per coin — ``Harshly Cleaned``, ``Gold``.

    So ``MS63 FB`` becomes ``MS63 Full Bands`` with ``full_names``, and ``AU Details`` becomes
    ``AU Details — Harshly Cleaned`` with ``with_detail``. A sticker is no longer reduced to its
    issuer: a modifier the user named ``CAC Gold`` reads as ``CAC Gold``.
    """
    modifier = link.modifier
    if modifier is None:  # pragma: no cover - defensive
        return ""
    return render_pair(
        modifier,
        link.detail,
        with_detail=with_detail,
        full_names=full_names,
        with_issuer=with_issuer,
    )


def render(grade: SpecimenGrade, display: GradeDisplay | None = None) -> str:
    """Render a grade for a column or a list."""
    display = display or GradeDisplay()
    pairs = [
        (link.modifier, link.detail)
        for link in _ordered(grade.modifier_links)
        if link.modifier is not None
    ]
    text = assemble(grade.grade_label or "", pairs, display)

    extras: list[str] = []
    if display.scale and grade.scale is not None:
        extras.append(grade.scale.code)
    if display.source:
        extras.append(grade.source)
    if display.assigned_by and grade.assigned_by and not grade.hide_assigned_by:
        extras.append(grade.assigned_by)
    if extras:
        text = f"{text} [{', '.join(extras)}]" if text else f"[{', '.join(extras)}]"
    return text


def is_problem_grade(grade: SpecimenGrade) -> bool:
    """Whether this grade carries a problem qualifier such as ``Details``."""
    return any(
        link.modifier is not None and link.modifier.kind in PROBLEM_KINDS
        for link in grade.modifier_links
    )


def grade_sort_key(grade: SpecimenGrade) -> tuple[int, float]:
    """Sort key placing ungraded coins last.

    A tuple so ``None`` is never compared with a float.
    """
    if grade.normalised is None:
        return (1, 0.0)
    return (0, -grade.normalised)


def suggest_base_value(label: str) -> float | None:
    """A first guess at what a typed grade counts as, from any number in it.

    ``MS63`` suggests 63 and ``8`` suggests 8, which covers the numeric scales. Anything else
    returns nothing and the user says what it is worth — guessing at ``gVF`` would be inventing
    a number and calling it theirs.
    """
    digits = "".join(character for character in label if character.isdigit())
    if not digits:
        return None
    try:
        return float(digits)
    except ValueError:  # pragma: no cover - defensive
        return None
