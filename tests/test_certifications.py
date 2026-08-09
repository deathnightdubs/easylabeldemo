"""Certification: concurrent certifications, a single primary, and grading history."""

from __future__ import annotations

from datetime import date


def test_a_new_library_has_no_grading_companies(svc):
    from numis.models import GradingCompany

    assert svc.session.query(GradingCompany).count() == 0


def test_two_certifications_can_be_current_at_once(svc, modern):
    """A grading company's slab plus a separate endorsement sticker is normal now."""
    coin = svc.add_specimen(modern, display_name="Morgan")
    ngc = svc.create_grading_company("NGC", "Numismatic Guaranty Company")
    cac = svc.create_grading_company("CAC", "Certified Acceptance Corporation")

    svc.add_certification(coin, ngc, cert_number="2871554-013", rank=1)
    svc.add_certification(coin, cac)  # endorsements often have no number of their own

    assert len(svc.current_certifications(coin)) == 2


def test_certifications_queue_in_order_of_precedence(svc, modern):
    coin = svc.add_specimen(modern)
    ngc = svc.create_grading_company("NGC", "NGC")
    first = svc.add_certification(coin, ngc, cert_number="1")
    second = svc.add_certification(coin, ngc, cert_number="2")
    assert (first.rank, second.rank) == (1, 2)
    assert svc.primary_certification(coin) is first

    svc.reorder([second, first])
    assert svc.primary_certification(coin) is second


def test_certification_number_may_be_absent(svc, modern):
    coin = svc.add_specimen(modern)
    cac = svc.create_grading_company("CAC", "CAC")
    certification = svc.add_certification(coin, cac)
    assert certification.cert_number is None


def test_duplicate_certification_number_warns_but_does_not_refuse(svc, modern):
    """Warn, do not block: a collector must never be stopped mid-entry."""
    ngc = svc.create_grading_company("NGC", "NGC")
    first = svc.add_specimen(modern, display_name="first")
    second = svc.add_specimen(modern, display_name="second")

    svc.add_certification(first, ngc, cert_number="2871554-013")
    saved = svc.add_certification(second, ngc, cert_number="2871554-013")

    assert saved.id is not None  # it saved
    assert [w.code for w in svc.warnings] == ["duplicate_cert_number"]
    assert "already recorded" in svc.warnings[0].message


def test_grading_history_is_a_chain(svc, modern):
    """Cracking out and regrading is routine, so the trail has to survive."""
    coin = svc.add_specimen(modern, display_name="Morgan")
    ngc = svc.create_grading_company("NGC", "NGC")

    old = svc.add_certification(
        coin, ngc, cert_number="111111-001", graded_on=date(2019, 4, 2), status="cracked_out"
    )
    new = svc.add_certification(
        coin, ngc, cert_number="222222-002", graded_on=date(2024, 11, 15),
        rank=1, supersedes=old,
    )

    history = svc.certification_history(coin)
    assert [c.cert_number for c in history] == ["111111-001", "222222-002"]
    assert new.supersedes_id == old.id
    assert len(svc.current_certifications(coin)) == 1


def test_an_explicit_outcome_is_not_overwritten_by_superseding(svc, modern):
    """If the user recorded *how* it ended, that is more specific than 'superseded'."""
    coin = svc.add_specimen(modern)
    ngc = svc.create_grading_company("NGC", "NGC")
    old = svc.add_certification(coin, ngc, cert_number="1", status="cracked_out")
    svc.add_certification(coin, ngc, cert_number="2", supersedes=old)
    assert old.status == "cracked_out"


def test_a_current_certification_becomes_superseded(svc, modern):
    coin = svc.add_specimen(modern)
    ngc = svc.create_grading_company("NGC", "NGC")
    old = svc.add_certification(coin, ngc, cert_number="1", status="current")
    svc.add_certification(coin, ngc, cert_number="2", supersedes=old)
    assert old.status == "superseded"


def test_a_certification_carries_its_grade_onto_the_shared_axis(svc, modern, sheldon):
    coin = svc.add_specimen(modern)
    ngc = svc.create_grading_company("NGC", "NGC")
    grade = svc.add_grade(coin, sheldon, "MS63", base_value=63.0, source="tpg", assigned_by="NGC")
    certification = svc.add_certification(coin, ngc, cert_number="1", grade=grade, rank=1)
    assert certification.specimen_grade_id == grade.id
    assert certification.grade.normalised == 63.0
