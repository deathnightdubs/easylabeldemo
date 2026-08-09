"""Shared fixtures.

Every fixture builds only what its test needs. There is deliberately no "sample collection"
fixture full of catalogues and grading scales: the application ships empty, and fixtures that
quietly supply content would hide whether the code really requires it.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from numis.db import Library, memory_library
from numis.services import CollectionService


@pytest.fixture
def library() -> Library:
    lib = memory_library()
    yield lib
    lib.close()


@pytest.fixture
def session(library: Library) -> Session:
    session = library.session_factory()
    yield session
    session.rollback()
    session.close()


@pytest.fixture
def svc(session: Session) -> CollectionService:
    return CollectionService(session)


@pytest.fixture
def modern(svc: CollectionService):
    return svc.create_subcollection("Modern")


@pytest.fixture
def ancients(svc: CollectionService):
    return svc.create_subcollection("Ancients")


#: Grades a user might type on each scale, and what they said each was worth. Nothing is
#: pre-registered: a grade is typed with its base value, so these are just test data.
SHELDON_VALUES = {"MS64": 64.0, "MS63": 63.0, "MS62": 62.0, "AU58": 58.0, "XF45": 45.0}
ADJECTIVAL_VALUES = {"UNC": 60.0, "AU": 53.0, "XF": 42.5, "VF": 27.5, "F": 13.5}
CHINESE_VALUES = {"10": 63.0, "8": 50.0, "6": 35.0, "4": 20.0}


@pytest.fixture
def sheldon(svc: CollectionService):
    """A user-defined scale. The application ships with none, and a scale needs no levels."""
    return svc.create_grade_scale("SHELDON", "Sheldon 1-70", kind="numeric")


@pytest.fixture
def adjectival(svc: CollectionService):
    return svc.create_grade_scale("ADJ", "Adjectival")


@pytest.fixture
def chinese10(svc: CollectionService):
    return svc.create_grade_scale("CN10", "Chinese 1-10", kind="numeric")


@pytest.fixture
def modifiers(svc: CollectionService) -> dict[str, object]:
    """Grade modifiers of every kind, with the deltas the design settled on."""
    return {
        "DETAILS": svc.create_grade_modifier("DETAILS", "Details", "detail", -0.4),
        "CAC": svc.create_grade_modifier(
            "CAC", "CAC sticker", "sticker", 0.15, issuer="CAC", abbreviation="CAC"
        ),
        "WINGS": svc.create_grade_modifier(
            "WINGS", "WINGS sticker", "sticker", 0.10, issuer="WINGS", abbreviation="WNG"
        ),
        "PLUS": svc.create_grade_modifier(
            "PLUS", "+", "qualifier", 0.25, attach_without_space=True
        ),
        "STAR": svc.create_grade_modifier(
            "STAR", "Star", "qualifier", 0.20, abbreviation="*", attach_without_space=True
        ),
        "FB": svc.create_grade_modifier("FB", "Full Bands", "strike", 0.15, abbreviation="FB"),
        "BN": svc.create_grade_modifier("BN", "Brown", "colour", 0.0, abbreviation="BN"),
    }
