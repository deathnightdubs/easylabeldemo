"""Regressions for four faults reported from a real run of the detail panel.

Each of these was visible to the user rather than to the test suite, which is the point: the
panel wires dialogs to services by hand, and a wrong argument there fails silently because Qt
swallows the exception on its way out of a slot. The tests drive the panel's own buttons and
stub only ``exec``, so the wiring under test is the real wiring.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QDialog
from sqlalchemy import select

from numis import grading
from numis.db import create_library
from numis.models import Certification, SpecimenGrade, SpecimenGradeModifier
from numis.ui import detail_panel as panel_module
from numis.ui.detail_panel import CatalogueReferenceDialog, CertificationDialog
from numis.ui.grade_dialog import GradeDialog
from numis.ui.main_window import MainWindow

ACCEPTED = QDialog.DialogCode.Accepted


@pytest.fixture
def window(qapp, tmp_path):
    library = create_library(tmp_path / "Bugfixes.numis")
    win = MainWindow(library)
    service = win.service
    modern = service.create_subcollection("Modern")
    service.add_specimen(modern, display_name="Test coin")
    win.session.commit()
    win._reload_subcollections(keep="Modern")
    win.view.selectRow(0)
    # Failures are reported to the window as a modal, which would block a headless run.
    win.detail.failed.disconnect()
    yield win
    win.session.close()
    library.close()


def _reports(window) -> list[str]:
    """Collect what the panel reports instead of showing a dialog."""
    reported: list[str] = []
    window.detail.failed.connect(reported.append)
    return reported


def _only_certification(service, specimen_id: int) -> Certification:
    return service.session.scalars(
        select(Certification).where(Certification.specimen_id == specimen_id)
    ).one()


class TestGradeAddButton:
    """Reported: “pressing the add button for the grades does nothing”.

    The panel called ``GradeDialog(self.service, self)``, so the panel itself arrived where a
    grade to edit was expected and loading it raised inside the constructor.
    """

    def test_the_add_button_records_a_grade(self, window, monkeypatch):
        service, detail = window.service, window.detail
        seen: list[object] = []

        class Filled(GradeDialog):
            def __init__(self, svc, grade=None, parent=None):
                seen.append(grade)
                super().__init__(svc, grade, parent)
                self.label.setText("MS63")
                self.base_value.setValue(63.0)
                self.assigned_by.setText("NGC")

            def exec(self):
                return ACCEPTED

        monkeypatch.setattr(panel_module, "GradeDialog", Filled)
        reported = _reports(window)

        detail.grades.add_button.click()

        assert reported == []
        assert seen == [None], "a new grade must not be handed an existing one to edit"
        assert detail.grades.list.count() == 1
        grade = service.grades_for(detail.specimen)[0]
        assert grade.grade_label == "MS63"
        assert grade.base_value == pytest.approx(63.0)
        assert grade.normalised == pytest.approx(63.0)

    def test_the_second_argument_is_a_grade_not_the_panel(self, window, monkeypatch):
        """Guards the exact mistake: a widget passed where a grade belongs."""
        captured: list[object] = []

        class Recording(GradeDialog):
            def __init__(self, svc, grade=None, parent=None):
                captured.append((grade, parent))
                super().__init__(svc, grade, parent)

            def exec(self):
                return QDialog.DialogCode.Rejected

        monkeypatch.setattr(panel_module, "GradeDialog", Recording)
        window.detail.grades.add_button.click()

        grade, parent = captured[0]
        assert grade is None
        assert parent is window.detail

    def test_a_grade_can_be_edited_from_the_panel(self, window, monkeypatch):
        service, detail = window.service, window.detail
        grade = service.add_grade(
            detail.specimen, None, "MS62", base_value=62.0, assigned_by="NGC"
        )
        window.session.commit()
        detail.refresh()

        class Edited(GradeDialog):
            def __init__(self, svc, existing=None, parent=None):
                super().__init__(svc, existing, parent)
                assert existing is not None, "editing must load the chosen grade"
                self.label.setText("MS64")
                self.base_value.setValue(64.0)

            def exec(self):
                return ACCEPTED

        monkeypatch.setattr(panel_module, "GradeDialog", Edited)
        detail.grades.list.setCurrentRow(0)
        detail._edit(SpecimenGrade, detail.grades)

        assert grade.grade_label == "MS64"
        assert grade.normalised == pytest.approx(64.0)

    def test_an_empty_grade_is_refused_without_touching_the_coin(self, window, monkeypatch):
        detail = window.detail

        class Blank(GradeDialog):
            def exec(self):
                return ACCEPTED

            def validate(self):  # what the real dialog does after warning the user
                return False

        monkeypatch.setattr(panel_module, "GradeDialog", Blank)
        detail.grades.add_button.click()
        assert detail.grades.list.count() == 0


class TestCatalogueDialogHasNoPrimaryCheckbox:
    """Reported: “the show this one first checkbox is still in there. Isn't it obsolete now?”

    It is: rank decides precedence, and it is reordered with the arrows in the panel.
    """

    def test_the_checkbox_is_gone(self, window):
        dialog = CatalogueReferenceDialog(window.service, window.detail)
        assert not hasattr(dialog, "primary")

    def test_the_certification_dialog_lost_it_too(self, window):
        dialog = CertificationDialog(window.service, window.detail.specimen, window.detail)
        assert not hasattr(dialog, "primary")

    def test_rank_still_decides_what_a_single_value_column_shows(self, window):
        service, detail = window.service, window.detail
        krause = service.create_catalog("KM", "Krause")
        hartill = service.create_catalog("H", "Hartill")
        window.session.commit()
        service.add_reference(detail.specimen, krause, "1866")
        second = service.add_reference(detail.specimen, hartill, "1.01")
        window.session.commit()

        assert service.primary_reference(detail.specimen).catalog_id == krause.id
        service.reorder([second, service.references_for(detail.specimen)[0]])
        window.session.commit()
        assert service.primary_reference(detail.specimen).catalog_id == hartill.id


class TestAddingCatalogueNumbers:
    """Reported: “I am still getting an error whenever I try to add catalog numbers”.

    ``add_reference`` lost ``is_primary`` when rank replaced it, but the panel kept passing it.
    """

    def test_a_catalogue_number_is_added_without_error(self, window, monkeypatch):
        service, detail = window.service, window.detail
        krause = service.create_catalog("KM", "Krause")
        window.session.commit()

        class Filled(CatalogueReferenceDialog):
            def __init__(self, svc, parent=None):
                super().__init__(svc, parent)
                self.catalogue.setCurrentIndex(self.catalogue.findData(krause.id))
                self.number.setText("2073")

            def exec(self):
                return ACCEPTED

        monkeypatch.setattr(panel_module, "CatalogueReferenceDialog", Filled)
        reported = _reports(window)

        detail.catalogues.add_button.click()

        assert reported == []
        assert detail.catalogues.list.count() == 1
        assert "KM 2073" in detail.catalogues.list.item(0).text()

    def test_add_reference_no_longer_accepts_is_primary(self, window):
        """The keyword is gone for good, so nothing can quietly pass it again."""
        service, detail = window.service, window.detail
        krause = service.create_catalog("KM", "Krause")
        window.session.commit()
        with pytest.raises(TypeError):
            service.add_reference(detail.specimen, krause, "2073", is_primary=True)


class TestStickersAreCertificationsInTheirOwnRight:
    """Reported: a sticker should be addable *as* the certification, picked like a grade.

    CAC does not grade the coin; it endorses somebody else's grade. So the certification
    dialog offers one “Awarded for” list holding both the coin's grades and the sticker
    instances sitting on them.
    """

    def _coin_with_a_stickered_grade(self, window):
        service, detail = window.service, window.detail
        service.create_grade_modifier("CAC", "CAC sticker", "sticker", 0.5, issuer="CAC")
        grade = service.add_grade(
            detail.specimen, None, "MS63", base_value=63.0,
            modifiers=[("CAC", "Gold")], assigned_by="PCGS",
        )
        window.session.commit()
        detail.refresh()
        return grade

    def test_the_awarded_for_list_offers_grades_and_stickers(self, window):
        grade = self._coin_with_a_stickered_grade(window)
        dialog = CertificationDialog(window.service, window.detail.specimen, window.detail)

        labels = [dialog.awarded.itemText(i) for i in range(dialog.awarded.count())]
        link = grade.modifier_links[0]

        assert dialog.select_awarded("grade", grade.id)
        assert dialog.awarded_for() == ("grade", grade.id)
        assert dialog.select_awarded("sticker", link.id)
        assert dialog.awarded_for() == ("sticker", link.id)
        assert any(text.startswith("sticker: CAC") and "Gold" in text for text in labels)
        assert any("MS63" in text for text in labels)

    def test_nothing_in_particular_is_the_default(self, window):
        self._coin_with_a_stickered_grade(window)
        dialog = CertificationDialog(window.service, window.detail.specimen, window.detail)
        assert dialog.awarded.currentIndex() == 0
        assert dialog.awarded_for() is None

    def test_the_separate_sticker_fields_are_gone(self, window):
        self._coin_with_a_stickered_grade(window)
        dialog = CertificationDialog(window.service, window.detail.specimen, window.detail)
        assert not hasattr(dialog, "sticker")
        assert not hasattr(dialog, "sticker_detail")
        assert hasattr(dialog, "awarded")

    def test_picking_a_sticker_ties_it_to_the_new_certification(self, window, monkeypatch):
        service, detail = window.service, window.detail
        grade = self._coin_with_a_stickered_grade(window)
        link_id = grade.modifier_links[0].id
        cac = service.create_grading_company("CAC", "Certified Acceptance Corporation")
        window.session.commit()

        class Filled(CertificationDialog):
            def __init__(self, svc, specimen, parent=None):
                super().__init__(svc, specimen, parent)
                self.company.setCurrentIndex(self.company.findData(cac.id))
                self.number.setText("CAC-991")
                assert self.select_awarded("sticker", link_id)

            def exec(self):
                return ACCEPTED

        monkeypatch.setattr(panel_module, "CertificationDialog", Filled)
        reported = _reports(window)

        detail.certifications.add_button.click()

        assert reported == []
        certification = _only_certification(service, detail.specimen.id)
        assert certification.cert_number == "CAC-991"
        link = service.session.get(SpecimenGradeModifier, link_id)
        assert link.certification_id == certification.id
        # The sticker endorses a grade it did not award, so the certification itself is not
        # the source of that grade.
        assert certification.specimen_grade_id is None

    def test_picking_a_grade_still_ties_the_grade(self, window, monkeypatch):
        service, detail = window.service, window.detail
        grade = self._coin_with_a_stickered_grade(window)
        pcgs = service.create_grading_company("PCGS", "PCGS")
        window.session.commit()
        grade_id = grade.id

        class Filled(CertificationDialog):
            def __init__(self, svc, specimen, parent=None):
                super().__init__(svc, specimen, parent)
                self.company.setCurrentIndex(self.company.findData(pcgs.id))
                self.number.setText("2871554-013")
                assert self.select_awarded("grade", grade_id)
                self.url.setText("https://example.invalid/verify/2871554-013")

            def exec(self):
                return ACCEPTED

        monkeypatch.setattr(panel_module, "CertificationDialog", Filled)
        detail.certifications.add_button.click()

        certification = _only_certification(service, detail.specimen.id)
        assert certification.specimen_grade_id == grade_id
        assert certification.verification_url.endswith("2871554-013")

    def test_a_sticker_still_reads_on_the_grade_it_endorses(self, window):
        """Tying the sticker to a company must not change how the grade reads."""
        grade = self._coin_with_a_stickered_grade(window)
        assert grading.render(grade) == "MS63 CAC"
        assert grading.render(grade, grading.GradeDisplay(modifiers=False)) == "MS63"
        detailed = grading.render(grade, grading.GradeDisplay(modifier_details=True))
        assert "CAC" in detailed and "Gold" in detailed
