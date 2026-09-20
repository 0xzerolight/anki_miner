"""Persian normalisation, folding and the script gate.

Every Persian character here is a \\N{NAME} escape: the authoring tools decode a
backslash-u escape into the literal character, which would put invisible
right-to-left text into this file (LEAD-BRIEF section 3).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.languages.fa import script as fa_script
from anki_miner.languages.fa.script import (
    FA_SENTENCE_RULES,
    PersianDictKeys,
    PersianScript,
    fa_fold,
    fa_normalize,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "fa"
ZWNJ = "\N{ZERO WIDTH NON-JOINER}"
# mi-ravam, "I go" (the PROBE word)
MI = "\N{ARABIC LETTER MEEM}\N{ARABIC LETTER FARSI YEH}"
RAVAM = "\N{ARABIC LETTER REH}\N{ARABIC LETTER WAW}\N{ARABIC LETTER MEEM}"
MIRAVAM = MI + ZWNJ + RAVAM
# miz, "table" — the word seperate_mi must leave alone
MIZ = MI + "\N{ARABIC LETTER ZAIN}"
# ketab, "book", and its plural ketab-ha
KETAB = "\N{ARABIC LETTER KEHEH}\N{ARABIC LETTER TEH}\N{ARABIC LETTER ALEF}\N{ARABIC LETTER BEH}"
HA = "\N{ARABIC LETTER HEH}\N{ARABIC LETTER ALEF}"
# "in" with the ARABIC (not Farsi) yeh, as a cp1256 file decodes it
THIS_ARABIC = "\N{ARABIC LETTER ALEF}\N{ARABIC LETTER YEH}\N{ARABIC LETTER NOON}"
THIS_FARSI = "\N{ARABIC LETTER ALEF}\N{ARABIC LETTER FARSI YEH}\N{ARABIC LETTER NOON}"
# xane, "house" — the ezafe spellings fold onto it
KHANE = "\N{ARABIC LETTER KHAH}\N{ARABIC LETTER ALEF}\N{ARABIC LETTER NOON}\N{ARABIC LETTER HEH}"

#: The verb forms the stub engine knows. FA_SEPARATE_MI_HOOK is module-level
#: mutable state, so every test that depends on it sets it explicitly: a lexicon
#: left behind by an earlier test on the same xdist worker would make the "miz is
#: not a verb" case pass for the wrong reason.
KNOWN_VERB_FORMS = frozenset(
    {
        MIRAVAM,
        "\N{ARABIC LETTER NOON}"
        + MI
        + ZWNJ
        + "\N{ARABIC LETTER DAL}\N{ARABIC LETTER ALEF}\N{ARABIC LETTER NOON}\N{ARABIC LETTER MEEM}",
    }
)


@pytest.fixture
def known_verbs(monkeypatch):
    monkeypatch.setattr(fa_script, "FA_SEPARATE_MI_HOOK", KNOWN_VERB_FORMS.__contains__)
    return KNOWN_VERB_FORMS


@pytest.fixture
def no_engine(monkeypatch):
    monkeypatch.setattr(fa_script, "FA_SEPARATE_MI_HOOK", None)


def _corpus() -> list[dict]:
    lines = (FIXTURES / "normalize.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line]


class TestNormalize:
    @pytest.mark.parametrize("row", _corpus(), ids=lambda row: row["note"])
    def test_the_committed_corpus(self, row, known_verbs):
        assert fa_normalize(row["raw"]) == row["normalized"], row["note"]

    def test_a_detached_prefix_becomes_a_zwnj_join(self, no_engine):
        # The affix rule alone does this; it needs no verb table.
        assert fa_normalize(MI + " " + RAVAM) == MIRAVAM

    def test_a_joined_prefix_is_split_only_for_a_known_verb(self, known_verbs):
        assert fa_normalize(MI + RAVAM) == MIRAVAM
        assert fa_normalize(MIZ) == MIZ

    def test_without_an_engine_a_joined_prefix_is_left_alone(self, no_engine):
        # Normalisation still runs on a fresh install; it just splits nothing.
        assert fa_normalize(MI + RAVAM) == MI + RAVAM
        assert fa_normalize(MIZ) == MIZ

    def test_a_detached_plural_suffix_becomes_a_zwnj_join(self, no_engine):
        assert fa_normalize(KETAB + " " + HA) == KETAB + ZWNJ + HA

    def test_arabic_letters_are_unified(self, no_engine):
        assert fa_normalize(THIS_ARABIC) == THIS_FARSI

    def test_digits_are_left_alone(self, no_engine):
        assert fa_normalize("\N{EXTENDED ARABIC-INDIC DIGIT FOUR}2") == "\N{EXTENDED ARABIC-INDIC DIGIT FOUR}2"

    def test_bidi_controls_are_stripped_and_the_zwnj_is_kept(self, no_engine):
        assert fa_normalize("\N{RIGHT-TO-LEFT MARK}" + MIRAVAM) == MIRAVAM

    def test_a_letter_repeated_three_times_collapses(self, no_engine):
        # xanehh -> xane: the run of three heh is one letter again. Two in a row
        # is left alone, because Persian does write those.
        assert fa_normalize(KHANE + "\N{ARABIC LETTER HEH}" * 2) == KHANE
        doubled = KETAB + "\N{ARABIC LETTER BEH}"
        assert fa_normalize(doubled) == doubled

    def test_the_normalised_sentence_is_the_stored_sentence(self, known_verbs):
        # Surfaces are slices of the NORMALISED line, so normalising twice is a
        # no-op or the parser's spans stop lining up.
        once = fa_normalize(MI + " " + RAVAM + " " + KETAB + " " + HA)
        assert fa_normalize(once) == once


class TestFold:
    def test_the_zwnj_spellings_share_one_key(self):
        assert fa_fold(MIRAVAM) == fa_fold(MI + RAVAM)

    def test_the_ezafe_spellings_share_one_key(self):
        assert fa_fold(KHANE + "\N{ARABIC HAMZA ABOVE}") == fa_fold(KHANE)
        assert fa_fold(KHANE[:-1] + "\N{ARABIC LETTER HEH WITH YEH ABOVE}") == fa_fold(KHANE)

    def test_the_arabic_and_farsi_yeh_share_one_key(self):
        assert fa_fold(THIS_ARABIC) == fa_fold(THIS_FARSI)

    def test_harakat_are_ignored(self):
        assert fa_fold(KETAB[0] + "\N{ARABIC KASRA}" + KETAB[1:]) == fa_fold(KETAB)

    def test_folding_is_idempotent(self):
        assert fa_fold(fa_fold(MIRAVAM)) == fa_fold(MIRAVAM)

    def test_dict_keys_reuse_the_same_function(self):
        keys = PersianDictKeys()
        assert keys.fold_term(MIRAVAM) == fa_fold(MIRAVAM)
        assert (
            keys.fold_reading("Ket\N{LATIN SMALL LETTER A WITH CIRCUMFLEX}b")
            == "ket\N{LATIN SMALL LETTER A WITH CIRCUMFLEX}b"
        )
        assert keys.fold_reading(None) is None

    def test_a_term_exact_row_wins_the_homograph_mask(self):
        keys = PersianDictKeys()
        rows = [(MIRAVAM, "to go"), (KETAB, "book")]
        assert keys.homograph_keep_mask(MIRAVAM, rows) == [True, False]
        assert keys.homograph_keep_mask(MIZ, rows) == [True, True]


class TestScript:
    def test_persian_letters_are_target_script(self):
        assert PersianScript().contains_target_script(MIRAVAM)

    def test_latin_and_digits_are_not(self):
        assert not PersianScript().contains_target_script("hello 123")
        assert not PersianScript().contains_target_script(
            "\N{EXTENDED ARABIC-INDIC DIGIT FOUR}\N{ARABIC-INDIC DIGIT TWO}"
        )

    @pytest.mark.parametrize(
        "letter",
        [
            "\N{ARABIC LETTER PEH}",
            "\N{ARABIC LETTER TCHEH}",
            "\N{ARABIC LETTER JEH}",
            "\N{ARABIC LETTER GAF}",
        ],
    )
    def test_persian_only_letters_are_inside_the_ranges(self, letter):
        assert PersianScript().contains_target_script(letter)

    def test_there_are_no_script_filter_options(self):
        script = PersianScript()
        assert script.filter_options() == ()
        assert script.matches("anything", MIRAVAM) is False


def test_sentence_rules_carry_the_persian_question_mark():
    assert "\N{ARABIC QUESTION MARK}" in FA_SENTENCE_RULES.terminators
    assert FA_SENTENCE_RULES.space_aware is True
    assert FA_SENTENCE_RULES.abbreviations == frozenset()
