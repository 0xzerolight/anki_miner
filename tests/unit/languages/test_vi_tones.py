"""The viet_text_tools tone-placement port (languages/vi/tones.py) and its MIT notice."""

from __future__ import annotations

import tomllib
import unicodedata
from pathlib import Path

import pytest

from anki_miner.languages.vi.tones import normalize_diacritics, to_new_style, to_old_style

ROOT = Path(__file__).resolve().parents[3]

#: Spec C.4's 13 fold pairs: new-style or misplaced spellings fold to old style; the last four stay put.
OLD_STYLE_PAIRS = [
    ("hoà", "hòa"),
    ("khoẻ", "khỏe"),
    ("thuỷ", "thủy"),
    ("hoá", "hóa"),
    ("toà", "tòa"),
    ("loè", "lòe"),
    ("huỷ", "hủy"),
    ("oà", "òa"),
    ("nghìên", "nghiền"),
    ("xoài", "xoài"),
    ("thuế", "thuế"),
    ("quý", "quý"),
    ("giày", "giày"),
]


@pytest.mark.parametrize(("given", "expected"), OLD_STYLE_PAIRS)
def test_the_old_style_fold(given, expected):
    assert to_old_style(given) == expected


def test_the_upstream_cases_still_hold():
    """viet_text_tools 0.1.6 tests/test_things.py, NormalizeDiacriticsTestCase, verbatim."""
    assert normalize_diacritics("hoạ") == "họa"
    assert normalize_diacritics("choàng") == "choàng"
    assert normalize_diacritics("thuỷ") == "thủy"
    assert normalize_diacritics("oà") == "òa"
    assert normalize_diacritics("toà") == "tòa"
    assert normalize_diacritics("toàn") == "toàn"
    assert normalize_diacritics("tòan") == "toàn"
    assert normalize_diacritics("ngòăng", new_style=True) == "ngoằng"
    assert normalize_diacritics("họa", new_style=True) == "hoạ"
    assert normalize_diacritics("chòang", new_style=True) == "choàng"
    assert normalize_diacritics("giừơng", new_style=True) == "giường"
    assert normalize_diacritics("baỷ", new_style=True) == "bảy"
    assert normalize_diacritics("cuả", new_style=True) == "của"
    assert normalize_diacritics("òa", new_style=True) == "oà"
    assert normalize_diacritics("toàn", new_style=True) == "toàn"


def test_both_directions_are_idempotent_and_keep_the_length():
    line = "Chúng ta cần hoà bình, sức khoẻ và thuỷ thủ."
    for fold in (to_old_style, to_new_style):
        once = fold(line)
        assert fold(once) == once
        assert len(once) == len(line)  # the tagging copy (Task 7) relies on this
    assert to_old_style(line) == "Chúng ta cần hòa bình, sức khỏe và thủy thủ."
    assert to_new_style(to_old_style(line)) == line


def test_case_and_decomposed_input():
    assert to_old_style("HOÀ BÌNH") == "HÒA BÌNH"
    assert to_old_style(unicodedata.normalize("NFD", "thuỷ")) == "thủy"
    assert to_new_style("Thủy") == "Thuỷ"


def test_the_mit_notice_ships_with_the_port():
    notice = (ROOT / "licenses" / "viet_text_tools" / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in notice and "Copyright (c) 2020 enricobarzetti" in notice
    readme = (ROOT / "licenses" / "viet_text_tools" / "README.md").read_text(encoding="utf-8")
    assert "anki_miner/languages/vi/tones.py" in readme
    with (ROOT / "pyproject.toml").open("rb") as handle:
        license_files = tomllib.load(handle)["project"]["license-files"]
    assert {"licenses/viet_text_tools/LICENSE", "licenses/viet_text_tools/README.md"} <= set(license_files)
    wheel_check = (ROOT / "scripts" / "check_wheel_assets.py").read_text(encoding="utf-8")
    assert '"licenses/viet_text_tools/LICENSE",' in wheel_check
    assert '"licenses/viet_text_tools/README.md",' in wheel_check
