"""Persian content typography: right-to-left, Arabic script, Vazirmatn as the last resort.

The asset sites are checked with the seam's OWN helper rather than a copy, so fa cannot drift
from the contract every other face is held to. The registry sweep in
``tests/unit/gui/test_content_style_contract.py`` covers fa from Task 14; this file covers it now.
"""

from __future__ import annotations

from PyQt6.QtGui import QFontDatabase

from anki_miner.languages.fa.style import FA_CONTENT_STYLE, FA_FONT_FAMILIES
from tests.unit.gui.test_content_style_contract import FONTS_DIR, ROOT, face_asset_problems

FACE = "Vazirmatn-Regular.ttf"


def test_the_style_is_right_to_left_arabic_with_a_bundled_face():
    assert (FA_CONTENT_STYLE.direction, FA_CONTENT_STYLE.writing_system) == ("rtl", "Arabic")
    assert FA_CONTENT_STYLE.bundled_fallback == FACE
    assert FA_CONTENT_STYLE.writing_system in QFontDatabase.WritingSystem.__members__


def test_the_family_list_prefers_the_bundled_face_then_the_platform_ones():
    assert FA_FONT_FAMILIES[0] == "Vazirmatn"
    assert FA_CONTENT_STYLE.families == FA_FONT_FAMILIES
    # One face per desktop, so the probe finds a real Arabic family before the
    # bundled one is ever registered.
    assert {"Segoe UI", "Tahoma"} & set(FA_FONT_FAMILIES)  # Windows
    assert {"SF Arabic", "Geeza Pro"} & set(FA_FONT_FAMILIES)  # macOS
    assert {"Noto Sans Arabic", "Noto Naskh Arabic"} & set(FA_FONT_FAMILIES)  # Linux


def test_the_wrap_is_identity_because_persian_is_space_delimited():
    line = "\N{ARABIC LETTER KEHEH}\N{ARABIC LETTER TEH}\N{ARABIC LETTER ALEF}\N{ARABIC LETTER BEH}"
    assert FA_CONTENT_STYLE.wrap(line) == line
    assert FA_CONTENT_STYLE.wrap("") == ""


def test_the_face_ships_with_every_asset_site():
    """Contract C6: the file, its OFL, REQUIRED_ASSETS x2, license-files, package-data, PROVENANCE."""
    assert face_asset_problems(ROOT, FACE) == []


def test_qt_can_register_the_face_and_reads_its_family(qapp):
    del qapp  # a QApplication has to exist before the font database is touched
    font_id = QFontDatabase.addApplicationFont(str(ROOT / FONTS_DIR / FACE))
    assert font_id != -1
    assert any("Vazirmatn" in family for family in QFontDatabase.applicationFontFamilies(font_id))
