"""Persian POS tiers, their labels and the mined-form policy.

The label table is checked against tags DERIVED from the committed fixtures --
every tag the ladder's ``words.dat`` rung can reach plus the tiers the tokenizer
synthesises -- so a tag that turns up in real data without a label is a red test,
not a silent gap. Every Persian character is a \\N{NAME} escape (LEAD-BRIEF
section 3).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.languages.fa import script as fa_script
from anki_miner.languages.fa import tokenizer as fa_tokenizer
from anki_miner.languages.fa._hazm import data, lexicon
from anki_miner.languages.fa.morphology import (
    FA_ALLOWED_POS,
    FA_EXCLUDED_SUBTYPES,
    FA_POS_LABELS,
    PersianMinedForm,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "fa"
ZWNJ = "\N{ZERO WIDTH NON-JOINER}"

_ALEF = "\N{ARABIC LETTER ALEF}"
_BEH = "\N{ARABIC LETTER BEH}"
_TEH = "\N{ARABIC LETTER TEH}"
_REH = "\N{ARABIC LETTER REH}"
_FEH = "\N{ARABIC LETTER FEH}"
_KEHEH = "\N{ARABIC LETTER KEHEH}"
_NOON = "\N{ARABIC LETTER NOON}"
_HEH = "\N{ARABIC LETTER HEH}"

RA = _REH + _ALEF
RAFT = _REH + _FEH + _TEH
RAFTAN = RAFT + _NOON
KETAB = _KEHEH + _TEH + _ALEF + _BEH
KETABHA = KETAB + ZWNJ + _HEH + _ALEF

#: Probe P-2's tag inventory: every part of speech ``words.dat`` carries. "0"
#: (158,034 of 193,350 rows) is the untagged marker the loader turns into an
#: empty tuple, so it never reaches a token.
WORDS_DAT_TAGS = (
    "N",
    "AJ",
    "ADV",
    "V",
    "NUM",
    "PRO",
    "P",
    "POSTP",
    "CONJ",
    "DET",
    "INT",
    "CL",
    "RES",
    "PL",
    "AJC",
    "ZVR",
)

#: The tiers ``tokenizer._classify`` invents when no table answers.
SYNTHESISED_TAGS = ("unknown", "PUNCT", "NUM")


@pytest.fixture(scope="module")
def fa_lexicon():
    return lexicon.build(data.load(FIXTURES))


@pytest.fixture
def armed(fa_lexicon, monkeypatch):
    # FA_SEPARATE_MI_HOOK is module-level mutable state (notes/004).
    monkeypatch.setattr(fa_script, "FA_SEPARATE_MI_HOOK", fa_lexicon.is_known_verb_form)
    return fa_lexicon


def _corpus() -> list[dict]:
    lines = (FIXTURES / "tokens.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line]


class TestTiers:
    def test_only_the_four_content_classes_are_mined(self):
        assert FA_ALLOWED_POS == ("ADV", "AJ", "N", "V")

    def test_every_other_words_dat_tag_is_out_by_class(self):
        assert set(FA_ALLOWED_POS) <= set(WORDS_DAT_TAGS)
        assert sorted(set(WORDS_DAT_TAGS) - set(FA_ALLOWED_POS)) == [
            "AJC",
            "CL",
            "CONJ",
            "DET",
            "INT",
            "NUM",
            "P",
            "PL",
            "POSTP",
            "PRO",
            "RES",
            "ZVR",
        ]

    def test_the_synthesised_tiers_are_never_mineable(self):
        for tag in SYNTHESISED_TAGS:
            assert tag not in FA_ALLOWED_POS, tag

    def test_stopwords_are_excluded_and_colloquial_forms_are_not(self):
        assert FA_EXCLUDED_SUBTYPES == ("stopword",)
        # A colloquial spelling is a real word a learner wants; the register is
        # a card field (Task 10), not a reason to drop the token.
        assert "informal" not in FA_EXCLUDED_SUBTYPES


class TestLabels:
    def test_every_words_dat_tag_has_a_label(self):
        missing = [tag for tag in WORDS_DAT_TAGS if tag not in FA_POS_LABELS]
        assert missing == []

    def test_every_tag_the_committed_words_dat_carries_has_a_label(self):
        # The ladder's first rung hands a words.dat tag straight to pos1, so the
        # table has to cover whatever the real file says, not a hand-kept list.
        reachable = {tag for tags in data.load(FIXTURES).words.values() for tag in tags}
        assert reachable
        assert sorted(tag for tag in reachable if tag not in FA_POS_LABELS) == []

    def test_every_tag_the_corpus_emits_has_a_label(self, armed):
        emitted = {
            token.feature.pos1 for row in _corpus() for token in fa_tokenizer.to_duck_tokens(row["normalized"], armed)
        }
        # POSTP is the tag ra carries and the corpus does emit it.
        assert "POSTP" in emitted
        assert sorted(tag for tag in emitted if tag not in FA_POS_LABELS) == []

    def test_the_synthesised_tiers_have_labels_too(self):
        for tag in SYNTHESISED_TAGS:
            assert FA_POS_LABELS[tag]

    def test_both_subtypes_are_named(self):
        assert FA_POS_LABELS["stopword"]
        assert FA_POS_LABELS["informal"]


class TestMinedForm:
    def test_a_verb_mines_as_its_infinitive(self):
        assert PersianMinedForm().mined_form("V", RAFTAN, RAFTAN, RAFT) == RAFTAN

    def test_a_noun_mines_as_the_lemma_the_ladder_chose(self):
        # Never a second stem() pass: the stem IS the tier that matched.
        assert PersianMinedForm().mined_form("N", KETAB, KETAB, KETABHA) == KETAB

    def test_a_token_no_table_answered_mines_as_its_surface(self):
        assert PersianMinedForm().mined_form("unknown", "", "", RA) == RA

    def test_the_corpus_mined_forms_never_carry_a_past_present_pair(self, armed):
        policy = PersianMinedForm()
        for row in _corpus():
            for token in fa_tokenizer.to_duck_tokens(row["normalized"], armed):
                mined = policy.mined_form(token.feature.pos1, token.feature.lemma, token.feature.lemma, token.surface)
                assert "#" not in mined, token.surface
