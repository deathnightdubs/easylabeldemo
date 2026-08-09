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
* several combine in order:                     ``MS63 FB BN``
* a detail is named only when asked for:        ``AU Details`` or ``AU Details — Harshly Cleaned``
* a sticker shows its issuer, then its value:   ``MS63 CAC`` or ``MS63 CAC Gold``
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
    #: Spell out what each modifier says: ``Details — Harshly Cleaned``, ``CAC Gold``.
    modifier_details: bool = False
    scale: bool = False
    source: bool = False
    assigned_by: bool = False

    @classmethod
    def compact(cls) -> GradeDisplay:
        return cls()

    @classmethod
    def full(cls) -> GradeDisplay:
        return cls(
            modifiers=True, modifier_details=True, scale=True, source=True, assigned_by=True
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
    def key(link: SpecimenGradeModifier) -> tuple[int, int, str]:
        kind = link.modifier.kind if link.modifier else ""
        position = KIND_ORDER.index(kind) if kind in KIND_ORDER else len(KIND_ORDER)
        return (position, link.sort_order, link.modifier.code if link.modifier else "")

    return sorted(links, key=key)


def render_modifier(link: SpecimenGradeModifier, *, with_detail: bool) -> str:
    """How one modifier reads.

    ``with_detail`` is the difference between ``CAC`` and ``CAC Gold``, and between ``Details``
    and ``Details — Harshly Cleaned``.
    """
    modifier = link.modifier
    if modifier is None:  # pragma: no cover - defensive
        return ""

    if modifier.kind == "sticker":
        # A sticker is somebody's endorsement, so the issuer is the useful part; the value
        # (green, gold) only matters when the detail is wanted.
        head = modifier.issuer or modifier.short
        if with_detail and link.detail:
            return f"{head} {link.detail}"
        return head

    if modifier.kind == "detail":
        # Always reads as the modifier's own name, because 'Details' is the recognised term;
        # what the problem actually was follows only when asked for.
        if with_detail and link.detail:
            return f"{modifier.label} — {link.detail}"
        return modifier.label

    if with_detail and link.detail:
        return f"{modifier.short} {link.detail}"
    return modifier.short


def render(grade: SpecimenGrade, display: GradeDisplay | None = None) -> str:
    """Render a grade for a column or a list."""
    display = display or GradeDisplay()
    text = grade.grade_label or ""

    if display.modifiers:
        for link in _ordered(grade.modifier_links):
            piece = render_modifier(link, with_detail=display.modifier_details)
            if not piece:
                continue
            if link.modifier is not None and link.modifier.attach_without_space:
                text += piece
            else:
                text = f"{text} {piece}" if text else piece

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
