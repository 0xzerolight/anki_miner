"""Hebrew content typography: right-to-left, Hebrew script, Noto Sans Hebrew as the last resort.

The asset sites are checked with the seam's OWN helper rather than a copy, so he cannot drift from
the contract every other face is held to (``tests/unit/gui/test_content_style_contract.py`` sweeps
the registry and covers he once it is registered; this file covers it now).
"""

from __future__ import annotations

import hashlib

from PyQt6.QtGui import QFontDatabase

from anki_miner.languages.he.style import HE_CONTENT_STYLE, HE_FONT_FAMILIES
from tests.unit.gui.test_content_style_contract import FONTS_DIR, ROOT, face_asset_problems

FACE = "NotoSansHebrew-Regular.ttf"
#: The hinted static build of NotoSansHebrew-v3.001, byte-for-byte upstream.
FACE_SHA256 = "cdefaf8efd47045f6820928eba84db5bed7557539328952b5f828315485e02ee"
FACE_BYTES = 26860


def test_the_style_is_right_to_left_hebrew_with_a_bundled_face():
    assert (HE_CONTENT_STYLE.direction, HE_CONTENT_STYLE.writing_system) == ("rtl", "Hebrew")
    assert HE_CONTENT_STYLE.bundled_fallback == FACE
    assert HE_CONTENT_STYLE.writing_system in QFontDatabase.WritingSystem.__members__


def test_the_family_list_names_one_real_family_per_desktop():
    assert HE_FONT_FAMILIES[0] == "Noto Sans Hebrew"
    assert HE_CONTENT_STYLE.families == HE_FONT_FAMILIES
    # One face per desktop, so the probe finds an installed Hebrew family before
    # the bundled one is ever registered.
    assert {"Segoe UI", "Tahoma"} & set(HE_FONT_FAMILIES)  # Windows
    assert {"Arial Hebrew", "SF Hebrew"} & set(HE_FONT_FAMILIES)  # macOS
    assert {"David CLM", "Frank Ruehl CLM", "DejaVu Sans"} & set(HE_FONT_FAMILIES)  # Linux


def test_the_wrap_is_identity_because_hebrew_is_space_delimited():
    line = "\N{HEBREW LETTER SHIN}\N{HEBREW LETTER LAMED}\N{HEBREW LETTER VAV}\N{HEBREW LETTER FINAL MEM}"
    assert HE_CONTENT_STYLE.wrap(line) == line
    assert HE_CONTENT_STYLE.wrap("") == ""


def test_the_face_ships_with_every_asset_site():
    """Contract C6: the file, its OFL, REQUIRED_ASSETS x2, license-files, package-data, PROVENANCE."""
    assert face_asset_problems(ROOT, FACE) == []


def test_the_face_is_the_upstream_artifact_byte_for_byte():
    data = (ROOT / FONTS_DIR / FACE).read_bytes()
    assert len(data) == FACE_BYTES
    assert hashlib.sha256(data).hexdigest() == FACE_SHA256
    assert FACE_SHA256 in (ROOT / FONTS_DIR / "PROVENANCE.md").read_text(encoding="utf-8")


def test_qt_can_register_the_face_and_reads_its_family(qapp):
    del qapp  # a QApplication has to exist before the font database is touched
    font_id = QFontDatabase.addApplicationFont(str(ROOT / FONTS_DIR / FACE))
    assert font_id != -1
    assert any("Noto Sans Hebrew" in family for family in QFontDatabase.applicationFontFamilies(font_id))
