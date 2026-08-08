"""Grading across incompatible standards.

Grades arrive in mutually unintelligible languages: ``MS63`` from a grading company, a plain
``AU`` from a dealer, ``8`` on a Chinese 1-10 scale, plus stickers and "details" qualifiers
for problem coins. Sorting them together needs one shared axis.

Every number on that axis is **user data**. The application ships with no scales, no levels
and no modifiers; the user defines what they use and says where each grade sits. This module
only does the arithmetic. See docs/design/02, Part 4.2.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import GradeLevel, GradeModifier, GradeScale, SpecimenGrade

#: Modifier kinds that mean the coin has a problem rather than a better appearance.
PROBLEM_KINDS = ("detail",)


def resolve_level(session: Session, scale: GradeScale, label: str) -> GradeLevel | None:
    """Find a level by label or alias, case-insensitively.

    Aliases exist so ``MS-63``, ``MS 63`` and ``Mint State 63`` all reach the same level
    without the user having to type one canonical spelling.
    """
    wanted = label.strip().lower()
    for level in session.scalars(
        select(GradeLevel).where(GradeLevel.grade_scale_id == scale.id)
    ):
        if level.label.strip().lower() == wanted:
            return level
        if level.aliases and any(
            alias.strip().lower() == wanted for alias in level.aliases.split("|")
        ):
            return level
    return None


def compute_normalised(
    base: float | None, modifiers: Iterable[GradeModifier] = ()
) -> float | None:
    """Position on the shared axis: the level's position plus every modifier's delta.

    A ``Details`` modifier carries a small negative delta, which is what keeps
    ``AU Details`` immediately below ``AU`` instead of at the bottom of the collection.
    """
    if base is None:
        return None
    return base + sum(modifier.normalised_delta for modifier in modifiers)


def describe(level_label: str, modifiers: Sequence[GradeModifier] = ()) -> str:
    """Render a grade the way it is usually written: ``MS63 CAC green``."""
    parts = [level_label, *[modifier.label for modifier in modifiers]]
    return " ".join(part for part in parts if part)


def is_problem_grade(grade: SpecimenGrade) -> bool:
    """Whether this grade carries a problem qualifier such as ``Details``."""
    return any(modifier.kind in PROBLEM_KINDS for modifier in grade.modifiers)


def grade_sort_key(grade: SpecimenGrade) -> tuple[int, float]:
    """Sort key placing ungraded coins last.

    Returned as a tuple so ``None`` never has to be compared with a float.
    """
    if grade.normalised is None:
        return (1, 0.0)
    return (0, -grade.normalised)
