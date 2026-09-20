"""vi keys (fold_term = dedup_fold) and the P3 normaliser (spec C.4, S3, S5)."""

from __future__ import annotations

import unicodedata

import pytest

from anki_miner.languages.vi.keys import VI_KEYS, vi_fold_term
from anki_miner.languages.vi.script import fold_tone_placement, vi_normalize

#: Judge finding 3: Latin names whose diacritics live in the port's tone class. The fold must not
#: move them; every one of these was corrupted by the unscoped fold in the stored sentence.
FOREIGN_NAMES = ["señor", "Núñez", "Béatrice", "Chloé", "Raúl", "Zoé", "café", "résumé"]


@pytest.mark.parametrize(
    ("given", "key"),
    [
        ("Hoà bình", "hòa bình"),
        ("hòa bình", "hòa bình"),
        ("SỨC KHOẺ", "sức khỏe"),
        ("Thuỷ thủ", "thủy thủ"),
        (unicodedata.normalize("NFD", "Bác sĩ"), "bác sĩ"),
        ("kỹ thuật", "kỹ thuật"),  # y/i variance is a ladder rung (Task 6), never a fold
    ],
)
def test_fold_term_is_nfc_casefold_old_style(given, key):
    assert vi_fold_term(given) == key == VI_KEYS.fold_term(given)


def test_the_fold_never_strips_a_diacritic():
    words = ["ma", "mà", "má", "mả", "mã", "mạ"]
    assert len({vi_fold_term(word) for word in words}) == 6


def test_the_fold_is_idempotent():
    for word in ("Hoà bình", "KHOẺ", "nghìên", "Đẹp đẽ"):
        assert vi_fold_term(vi_fold_term(word)) == vi_fold_term(word)


def test_readings_fold_to_nfc():
    """wty-vi-en carries no readings (0 of 77,838 rows); NFC is all a reading key needs."""
    assert VI_KEYS.fold_reading(None) is None
    assert VI_KEYS.fold_reading(unicodedata.normalize("NFD", "hòa")) == "hòa"


@pytest.mark.parametrize("name", FOREIGN_NAMES)
def test_a_non_vietnamese_latin_word_is_never_refolded(name):
    """Judge finding 3: ``señor`` -> ``senõr`` and ``Béatrice`` -> ``Beátrice`` reached the card."""
    assert fold_tone_placement(name) == name
    assert fold_tone_placement(name, new_style=True) == name
    assert vi_normalize(f"Chào {name} nhé.") == f"Chào {name} nhé."
    assert vi_fold_term(name) == name.casefold()


def test_the_guard_does_not_block_a_real_vietnamese_word_beside_a_name():
    assert vi_normalize("Señor Núñez uống thuỷ tinh hoà bình") == "Señor Núñez uống thủy tinh hòa bình"


def test_normalize_composes_repairs_and_folds_to_old_style():
    # A double space and an nbsp each become one space.
    raw = unicodedata.normalize("NFD", "Ðây là hoà bình") + "  và\u00a0sức khoẻ"
    assert vi_normalize(raw) == "Đây là hòa bình và sức khỏe"


def test_normalize_keeps_physical_lines_and_case():
    """clean_subtitle_text strips annotations per physical line AFTER normalize: newlines must survive."""
    assert vi_normalize("HOÀ BÌNH\nNghìên   ngẫm") == "HÒA BÌNH\nNghiền ngẫm"
