"""Hebrew normalise, the character predicates, the script gate and the sentence rules (spec F.2).

Every Hebrew, pointed and invisible literal comes from ``tests/fixtures/he/``: this module carries
no Hebrew character of its own, which is the rule the branch holds to (LEAD-BRIEF section 3).
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path

import pytest

from anki_miner.languages.he.script import (
    HE_LETTER_RANGES,
    HE_MARK_CLASS,
    HE_MARK_RANGES,
    HE_SENTENCE_RULES,
    HE_SUBTITLE_REGEX,
    HebrewDictKeys,
    HebrewScript,
    he_fold,
    he_normalize,
    is_he_letter,
    is_he_mark,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "he"
ENCODINGS = json.loads((FIXTURES / "encodings.json").read_text(encoding="utf-8"))
FOLD_SITES = [json.loads(line) for line in (FIXTURES / "fold_sites.jsonl").read_text(encoding="utf-8").splitlines()]
TOKEN_LINES = {
    row["id"]: row for row in (json.loads(line) for line in (FIXTURES / "tokens.jsonl").read_text("utf-8").splitlines())
}


# --------------------------------------------------------------------------
# The mark set: U+0591-U+05C7 is NOT one run of combining marks.
# --------------------------------------------------------------------------


def test_the_mark_ranges_are_exactly_the_combining_marks_of_the_block():
    """U+05BE is Pd and U+05C0/05C3/05C6 are Po, so the raw range is not the mark set."""
    covered = {code for low, high in HE_MARK_RANGES for code in range(low, high + 1)}
    expected = {code for code in range(0x0591, 0x05C8) if unicodedata.category(chr(code)) == "Mn"}
    assert covered == expected
    assert len(covered) == 51
    assert covered.isdisjoint({0x05BE, 0x05C0, 0x05C3, 0x05C6})


@pytest.mark.parametrize("code", [0x05BE, 0x05C0, 0x05C3, 0x05C6])
def test_the_four_punctuation_members_are_not_marks(code):
    assert not is_he_mark(chr(code))


def test_every_combining_mark_is_a_mark():
    assert all(is_he_mark(chr(code)) for code in range(0x0591, 0x05C8) if unicodedata.category(chr(code)) == "Mn")


def test_the_regex_class_is_derived_from_the_ordinals():
    """One source of truth: the class body and the predicate cannot drift."""
    import re

    pattern = re.compile("[" + HE_MARK_CLASS + "]")
    for low, high in HE_MARK_RANGES:
        for code in range(low, high + 1):
            assert pattern.fullmatch(chr(code)), hex(code)
    for code in (0x05BE, 0x05C0, 0x05C3, 0x05C6, 0x05D0):
        assert not pattern.fullmatch(chr(code)), hex(code)


def test_the_letter_ranges_hold_letters_only():
    for low, high in HE_LETTER_RANGES:
        for code in range(low, high + 1):
            assert unicodedata.category(chr(code)) == "Lo", hex(code)
    assert is_he_letter(chr(0x05D0))
    assert is_he_letter(chr(0x05DA))  # a final form is a letter like any other
    assert not is_he_letter(chr(0x05BE))
    assert not is_he_letter("A")


# --------------------------------------------------------------------------
# he_normalize (S5)
# --------------------------------------------------------------------------


def test_a_no_break_space_becomes_a_space():
    assert he_normalize(ENCODINGS["nbsp_line"]) == ENCODINGS["expected_after_normalize"]


def test_the_bidi_controls_windows_subtitle_tools_inject_are_removed():
    assert he_normalize(ENCODINGS["bidi_control_line"]) == ENCODINGS["expected_after_normalize"]


def test_the_two_joiners_survive_because_they_are_spelling():
    zwnj = "\N{ZERO WIDTH NON-JOINER}"
    zwj = "\N{ZERO WIDTH JOINER}"
    word = TOKEN_LINES["he01"]["tokens"][0][0]
    assert he_normalize(word + zwnj + word) == word + zwnj + word
    assert he_normalize(word + zwj + word) == word + zwj + word


def test_niqqud_survives_normalisation_because_the_stored_sentence_keeps_it():
    line = TOKEN_LINES["he02"]["sentence"]
    assert he_normalize(line) == unicodedata.normalize("NFC", line)
    assert any(is_he_mark(char) for char in he_normalize(line))


def test_the_maqaf_geresh_and_gershayim_are_never_normalised_away():
    for key in ("he03", "he05", "he06"):
        line = TOKEN_LINES[key]["sentence"]
        assert he_normalize(line) == unicodedata.normalize("NFC", line)


# --------------------------------------------------------------------------
# he_fold (R33, S3/S4)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("row", FOLD_SITES, ids=[row["note"] for row in FOLD_SITES])
def test_the_fold_matches_the_pinned_sites(row):
    assert he_fold(row["raw"]) == row["folded"]


@pytest.mark.parametrize("row", FOLD_SITES, ids=[row["note"] for row in FOLD_SITES])
def test_the_fold_is_idempotent(row):
    once = he_fold(row["raw"])
    assert he_fold(once) == once


def test_the_dict_keys_bind_the_same_function_at_both_seams():
    keys = HebrewDictKeys()
    for row in FOLD_SITES:
        assert keys.fold_term(row["raw"]) == he_fold(row["raw"])
    assert keys.fold_reading(None) is None
    assert keys.fold_reading("") == ""


def test_a_final_letter_is_never_folded_to_its_medial_form():
    [row] = [row for row in FOLD_SITES if "final kaf" in row["note"]]
    assert he_fold(row["raw"]).endswith(chr(0x05DA))


# --------------------------------------------------------------------------
# The script gate (S15)
# --------------------------------------------------------------------------


def test_the_gate_accepts_hebrew_and_rejects_latin_digits_and_bare_punctuation():
    script = HebrewScript()
    assert script.filter_options() == ()
    assert script.contains_target_script(TOKEN_LINES["he01"]["sentence"])
    assert not script.contains_target_script("Netflix")
    assert not script.contains_target_script("2024")
    assert not script.contains_target_script(chr(0x05BE) + chr(0x05F3) + chr(0x05F4))


def test_the_gate_admits_yiddish_which_is_what_the_first_switch_checklist_is_for():
    decks = json.loads((FIXTURES / "contamination_decks.json").read_text(encoding="utf-8"))
    script = HebrewScript()
    assert all(script.contains_target_script(front) for front in decks["yiddish_fronts"])


# --------------------------------------------------------------------------
# Sentence rules (S8) and the SDH filter (S10/R11)
# --------------------------------------------------------------------------


def test_the_dot_abbreviation_mechanism_is_inert_for_hebrew():
    """Hebrew abbreviations are built from gershayim and geresh and carry no dot."""
    assert HE_SENTENCE_RULES.abbreviations == frozenset()
    assert HE_SENTENCE_RULES.split_on_whitespace is False
    assert HE_SENTENCE_RULES.space_aware is True


def test_sof_pasuq_terminates_a_sentence():
    assert chr(0x05C3) in HE_SENTENCE_RULES.terminators
    assert {".", "!", "?"} <= HE_SENTENCE_RULES.terminators


def test_an_abbreviation_line_splits_into_two_sentences():
    from anki_miner.services.reading.sentence_splitter import split_sentences

    sentences = split_sentences(TOKEN_LINES["he05"]["sentence"], rules=HE_SENTENCE_RULES)
    assert len(sentences) == 2


def test_the_subtitle_filter_carries_no_speaker_label_rule():
    """Hebrew has no capitals, so a ``name:`` label cannot be told from speech."""
    from anki_miner.languages._spaced.script import BRACKETS_PATTERN, MUSIC_PATTERN, PARENS_PATTERN

    assert BRACKETS_PATTERN in HE_SUBTITLE_REGEX
    assert PARENS_PATTERN in HE_SUBTITLE_REGEX
    assert MUSIC_PATTERN in HE_SUBTITLE_REGEX
    assert "A-Z" not in HE_SUBTITLE_REGEX
