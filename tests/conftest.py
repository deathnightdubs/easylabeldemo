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


@pytest.fixture
def sheldon(svc: CollectionService):
    """A user-defined Sheldon scale. The application ships with no scales at all."""
    scale = svc.create_grade_scale("SHELDON", "Sheldon 1-70", kind="numeric")
    for label, normalised in [
        ("MS64", 64.0),
        ("MS63", 63.0),
        ("MS62", 62.0),
        ("AU58", 58.0),
        ("XF45", 45.0),
    ]:
        svc.add_grade_level(
            scale, label, normalised, numeric_value=float(label[2:]),
            aliases=f"{label[:2]}-{label[2:]}|{label[:2]} {label[2:]}",
        )
    return scale


@pytest.fixture
def adjectival(svc: CollectionService):
    scale = svc.create_grade_scale("ADJ", "Adjectival")
    for label, normalised in [("UNC", 60.0), ("AU", 53.0), ("XF", 42.5), ("VF", 27.5), ("F", 13.5)]:
        svc.add_grade_level(scale, label, normalised)
    return scale


@pytest.fixture
def chinese10(svc: CollectionService):
    scale = svc.create_grade_scale("CN10", "Chinese 1-10", kind="numeric")
    for label, normalised in [("10", 63.0), ("8", 50.0), ("6", 35.0), ("4", 20.0)]:
        svc.add_grade_level(scale, label, normalised, numeric_value=float(label))
    return scale


@pytest.fixture
def modifiers(svc: CollectionService) -> dict[str, object]:
    """Grade modifiers, with the deltas the design settled on."""
    return {
        "DETAILS": svc.create_grade_modifier("DETAILS", "Details", "detail", -0.4),
        "CACG": svc.create_grade_modifier("CACG", "CAC green", "sticker", 0.15),
        "CACGOLD": svc.create_grade_modifier("CACGOLD", "CAC gold", "sticker", 0.30),
        "PLUS": svc.create_grade_modifier("PLUS", "+", "qualifier", 0.25),
    }
