"""The Persian verb paradigms and the lexicon built over them.

Runs off the committed fixtures plus the two committed TSVs, so no pack is
needed. Every Persian character is a \\N{NAME} escape (LEAD-BRIEF section 3).
"""

from __future__ import annotations

import tracemalloc
from pathlib import Path

import pytest

from anki_miner.languages.fa import script as fa_script
from anki_miner.languages.fa._hazm import conjugation, data, lexicon

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "fa"
ZWNJ = "\N{ZERO WIDTH NON-JOINER}"

_ALEF = "\N{ARABIC LETTER ALEF}"
_ALEF_MADDA = "\N{ARABIC LETTER ALEF WITH MADDA ABOVE}"
_BEH = "\N{ARABIC LETTER BEH}"
_TEH = "\N{ARABIC LETTER TEH}"
_JEEM = "\N{ARABIC LETTER JEEM}"
_KHAH = "\N{ARABIC LETTER KHAH}"
_DAL = "\N{ARABIC LETTER DAL}"
_REH = "\N{ARABIC LETTER REH}"
_ZAIN = "\N{ARABIC LETTER ZAIN}"
_SEEN = "\N{ARABIC LETTER SEEN}"
_SHEEN = "\N{ARABIC LETTER SHEEN}"
_FEH = "\N{ARABIC LETTER FEH}"
_KEHEH = "\N{ARABIC LETTER KEHEH}"
_LAM = "\N{ARABIC LETTER LAM}"
_MEEM = "\N{ARABIC LETTER MEEM}"
_NOON = "\N{ARABIC LETTER NOON}"
_WAW = "\N{ARABIC LETTER WAW}"
_HEH = "\N{ARABIC LETTER HEH}"
_YEH = "\N{ARABIC LETTER FARSI YEH}"
_ARABIC_YEH = "\N{ARABIC LETTER YEH}"

MI = _MEEM + _YEH
# raftan, "to go": past stem raft, present stem ro
RAFT = _REH + _FEH + _TEH
RO = _REH + _WAW
RAFTAN = RAFT + _NOON
RAFTAM = RAFT + _MEEM
NARAFTAM = _NOON + RAFTAM
RAFTEAM = RAFT + _HEH + ZWNJ + _ALEF + _MEEM
MIRAVAM = MI + ZWNJ + RO + _MEEM
# miram / mi-ram, the colloquial "I go"; mordan, "to die", is its formal homograph
MIRAM = MI + _REH + _MEEM
MI_ZWNJ_RAM = MI + ZWNJ + _REH + _MEEM
MORDAN = _MEEM + _REH + _DAL + _NOON
# miz, "table": starts with mi and is not a verb
MIZ = MI + _ZAIN
# na, "no", and the bare noon the empty-past-stem row would produce
NA = _NOON + _HEH
# the words the bare imperative would have turned into rare verbs (P-9)
BALE = _BEH + _LAM + _HEH
BOLAND = _BEH + _LAM + _NOON + _DAL
BARE = _BEH + _REH + _HEH
BASTE = _BEH + _SEEN + _TEH + _HEH
# xune -> xane (hazm), ashiune -> ashiane (shekar)
XUNE = _KHAH + _WAW + _NOON + _HEH
XANE = _KHAH + _ALEF + _NOON + _HEH
ASHIUNE = _ALEF_MADDA + _SHEEN + _YEH + _WAW + _NOON + _HEH
ASHIANE = _ALEF_MADDA + _SHEEN + _YEH + _ALEF + _NOON + _HEH
UN = _ALEF + _WAW + _NOON
AN = _ALEF_MADDA + _NOON
# kar kardan, "to work": the light-verb pair
KAR = _KEHEH + _ALEF + _REH
KARDAN = _KEHEH + _REH + _DAL + _NOON
KETAB = _KEHEH + _TEH + _ALEF + _BEH
MAN = _MEEM + _NOON
THIS_ARABIC = _ALEF + _ARABIC_YEH + _NOON
THIS_FARSI = _ALEF + _YEH + _NOON
KOJAST = _KEHEH + _JEEM + _ALEF + _SEEN + _TEH


@pytest.fixture(scope="module")
def fa_lexicon():
    return lexicon.build(data.load(FIXTURES))


class TestPatterns:
    def test_the_pattern_set_is_the_probed_one(self):
        assert len(conjugation.PATTERNS) == 79
        assert len(set(conjugation.PATTERNS)) == 79

    def test_the_present_half_is_thirty_patterns(self):
        assert len(conjugation.PRESENT_PATTERNS) == 30
        assert set(conjugation.PRESENT_PATTERNS) <= set(conjugation.PATTERNS)

    def test_no_bare_imperative_pattern_exists(self):
        # Decision 2 / probe P-9: a pattern that is the prefix plus the bare
        # present stem would turn bale into an infinitive.
        bare = {conjugation.PRESENT.join(("", "")), _BEH + conjugation.PRESENT}
        assert not bare & set(conjugation.PATTERNS)

    def test_expanding_the_go_verb_covers_every_person(self):
        forms = set(conjugation.expand(RAFT, RO))
        assert {RAFTAM, NARAFTAM, RAFTEAM, MIRAVAM} <= forms
        assert RAFTAN in forms

    def test_the_infinitive_is_the_past_stem_plus_noon(self):
        assert conjugation.infinitive(RAFT + "#" + RO) == RAFTAN


class TestLexiconGuards:
    def test_a_malformed_verb_row_is_skipped(self):
        hast = "#" + _HEH + _SEEN + _TEH  # the empty-past-stem row, verbs.dat line 1
        spaced = _ZAIN + _YEH + _SEEN + _TEH + "# " + _ZAIN + _YEH + _WAW
        empty = lexicon.build(
            data.HazmData(
                words={},
                stopwords=frozenset(),
                verb_lines=(hast, spaced),
                iverb_rows=(),
                iwords={},
            )
        )
        assert empty.verb_form(NA) is None
        assert empty.verb_form(_NOON) is None
        assert empty.verb_count == 0

    def test_the_copula_row_never_produces_the_negative_particle(self, fa_lexicon):
        assert fa_lexicon.verb_form(NA) is None
        assert fa_lexicon.verb_form(_NOON) is None

    def test_no_bare_imperative_is_generated(self, fa_lexicon):
        # bale, boland and bare are the common words the bare imperative would
        # have turned into rare infinitives (P-9). baste is NOT in this list: it
        # is the real past participle of bastan, which the paradigms answer.
        for word in (BALE, BOLAND, BARE):
            assert fa_lexicon.verb_form(word) is None, word
        assert fa_lexicon.verb_form(BASTE) == _BEH + _SEEN + _TEH + _NOON

    def test_the_first_verbs_line_wins_a_homograph(self, fa_lexicon):
        # raft#ro (to go) is line 360, raft#rub (to sweep) line 361.
        assert fa_lexicon.verb_form(RAFTAM) == RAFTAN


class TestLexiconTables:
    def test_the_verb_table_is_the_probed_size(self, fa_lexicon):
        # 47,925 keys over the whole verbs.dat (probe P-3); the fixture carries
        # the file whole, so this is the real number.
        assert 47_000 <= fa_lexicon.verb_count <= 49_000

    def test_the_informal_table_beats_the_formal_one(self, fa_lexicon):
        assert fa_lexicon.informal_verb(MIRAM) == (RAFTAN, MIRAVAM)
        assert fa_lexicon.verb_form(MIRAM) == MORDAN

    def test_a_zwnj_less_informal_spelling_resolves(self, fa_lexicon):
        assert fa_lexicon.informal_verb(MI_ZWNJ_RAM) == fa_lexicon.informal_verb(MIRAM)

    def test_a_noun_that_starts_with_mi_is_not_a_verb(self, fa_lexicon):
        assert fa_lexicon.is_known_verb_form(MIZ) is False
        assert fa_lexicon.is_known_verb_form(MIRAVAM) is True

    def test_the_hook_is_asked_about_the_split_spelling_only(self, fa_lexicon):
        # seperate_mi splits miram into mi-ram and asks about THAT, so what
        # keeps the colloquial spelling intact is mi-ram being absent -- even
        # though miram itself is a formal form (of mordan, "to die", probe P-4).
        assert fa_lexicon.is_known_verb_form(MI_ZWNJ_RAM) is False
        assert fa_lexicon.verb_form(MIRAM) == MORDAN
        assert fa_lexicon.is_known_verb_form(MI + ZWNJ + _ZAIN) is False

    def test_the_colloquial_map_reaches_both_sources(self, fa_lexicon):
        assert fa_lexicon.colloquial(XUNE) == XANE
        assert fa_lexicon.colloquial(ASHIUNE) == ASHIANE
        assert fa_lexicon.colloquial(UN) == AN

    def test_the_compound_table_answers_pairs(self, fa_lexicon):
        assert fa_lexicon.compound(KAR, KARDAN) is True
        assert fa_lexicon.compound(KETAB, KARDAN) is False

    def test_the_present_stem_comes_back_from_the_infinitive(self, fa_lexicon):
        assert fa_lexicon.present_stem(RAFTAN) == RO
        assert fa_lexicon.present_stem(KETAB) is None

    def test_tags_and_stopwords_answer_from_words_dat(self, fa_lexicon):
        assert fa_lexicon.tags(KETAB)[0] == "N"
        assert fa_lexicon.tags(KOJAST) == ()
        assert fa_lexicon.is_stopword(MAN) is True
        assert fa_lexicon.is_stopword(KETAB) is False

    def test_the_tables_are_folded_as_well_as_raw(self, fa_lexicon):
        assert fa_lexicon.tags(THIS_ARABIC) == fa_lexicon.tags(THIS_FARSI)
        assert fa_lexicon.tags(THIS_FARSI) != ()

    def test_building_stays_under_the_memory_budget(self):
        loaded = data.load(FIXTURES)
        tracemalloc.start()
        built = lexicon.build(loaded)
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert built.verb_count
        assert peak < 40 * 1024 * 1024, peak


def test_building_arms_the_separate_mi_hook(monkeypatch):
    # FA_SEPARATE_MI_HOOK is module-level mutable state: set it to None first,
    # or this passes off whatever an earlier test on this worker left behind.
    monkeypatch.setattr(fa_script, "FA_SEPARATE_MI_HOOK", None)
    built = lexicon.build(data.load(FIXTURES))
    assert fa_script.FA_SEPARATE_MI_HOOK is not None
    assert fa_script.FA_SEPARATE_MI_HOOK(MIRAVAM) is True
    assert built.is_known_verb_form(MIRAVAM) is True
