"""Persian duck tokens: the ladder, the light-verb merge and the mined form.

Driven by ``tests/fixtures/fa/tokens.jsonl`` over the committed fixtures, so no
pack is needed. The corpus lists the CONTENT tokens of each line; punctuation is
pinned separately, below. Every Persian character is a \\N{NAME} escape
(LEAD-BRIEF section 3).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages import tagger_provider
from anki_miner.languages.fa import script as fa_script
from anki_miner.languages.fa import tokenizer as fa_tokenizer
from anki_miner.languages.fa._hazm import data, lexicon
from anki_miner.languages.fa.morphology import PersianMinedForm
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.services.tagger import LockedTagger
from anki_miner.services.word_filter import WordFilterService

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "fa"
ZWNJ = "\N{ZERO WIDTH NON-JOINER}"
NBSP = "\N{NO-BREAK SPACE}"

_ALEF = "\N{ARABIC LETTER ALEF}"
_BEH = "\N{ARABIC LETTER BEH}"
_TEH = "\N{ARABIC LETTER TEH}"
_DAL = "\N{ARABIC LETTER DAL}"
_REH = "\N{ARABIC LETTER REH}"
_SEEN = "\N{ARABIC LETTER SEEN}"
_SHEEN = "\N{ARABIC LETTER SHEEN}"
_FEH = "\N{ARABIC LETTER FEH}"
_GAF = "\N{ARABIC LETTER GAF}"
_KEHEH = "\N{ARABIC LETTER KEHEH}"
_KHAH = "\N{ARABIC LETTER KHAH}"
_MEEM = "\N{ARABIC LETTER MEEM}"
_NOON = "\N{ARABIC LETTER NOON}"
_WAW = "\N{ARABIC LETTER WAW}"
_HEH = "\N{ARABIC LETTER HEH}"
_YEH = "\N{ARABIC LETTER FARSI YEH}"
_ZAIN = "\N{ARABIC LETTER ZAIN}"

MI = _MEEM + _YEH
KAR = _KEHEH + _ALEF + _REH
KARDAN = _KEHEH + _REH + _DAL + _NOON
MIKONAM = MI + ZWNJ + _KEHEH + _NOON + _MEEM
KETAB = _KEHEH + _TEH + _ALEF + _BEH
MARD = _MEEM + _REH + _DAL
MORDAN = MARD + _NOON
DASHT = _DAL + _ALEF + _SHEEN + _TEH
DAD = _DAL + _ALEF + _DAL
SAXT = _SEEN + _KHAH + _TEH
SHEKAST = _SHEEN + _KEHEH + _SEEN + _TEH
XAST = _KHAH + _WAW + _ALEF + _SEEN + _TEH
KONAD = _KEHEH + _NOON + _DAL
BUD = _BEH + _WAW + _DAL
SHOD = _SHEEN + _DAL
KARD = _KEHEH + _REH + _DAL
GOFT = _GAF + _FEH + _TEH
RAFT = _REH + _FEH + _TEH
OFTAD = _ALEF + _FEH + _TEH + _ALEF + _DAL
ZAD = _ZAIN + _DAL
GEREFT = _GAF + _REH + _FEH + _TEH

#: The seven tagged words.dat rows that must stay nouns (probe P-9, judge B1).
TAGGED_NOUNS = (MARD, DASHT, DAD, SAXT, SHEKAST, XAST, KONAD)
#: The eight past stems the verb table must still answer: none is in words.dat.
VERB_ONLY = (BUD, SHOD, KARD, GOFT, RAFT, OFTAD, ZAD, GEREFT)


def _corpus() -> list[dict]:
    lines = (FIXTURES / "tokens.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line]


@pytest.fixture(scope="module")
def fa_lexicon():
    return lexicon.build(data.load(FIXTURES))


@pytest.fixture
def armed(fa_lexicon, monkeypatch):
    # FA_SEPARATE_MI_HOOK is module-level mutable state; set it explicitly so a
    # lexicon another test built cannot make these pass for the wrong reason.
    monkeypatch.setattr(fa_script, "FA_SEPARATE_MI_HOOK", fa_lexicon.is_known_verb_form)
    return fa_lexicon


def _content(tokens):
    return [token for token in tokens if token.feature.pos1 != "PUNCT"]


class TestCorpus:
    @pytest.mark.parametrize("row", _corpus(), ids=lambda row: row["note"][:40])
    def test_the_line_normalises_to_its_stored_spelling(self, row, armed):
        assert fa_script.fa_normalize(row["line"]) == row["normalized"], row["note"]

    @pytest.mark.parametrize("row", _corpus(), ids=lambda row: row["note"][:40])
    def test_every_token_matches_the_corpus(self, row, armed):
        produced = _content(fa_tokenizer.to_duck_tokens(row["normalized"], armed))
        assert len(produced) == len(row["tokens"]), [token.surface for token in produced]
        for token, expected in zip(produced, row["tokens"], strict=True):
            assert token.surface == expected["surface"], row["note"]
            assert token.feature.lemma == expected["lemma"], token.surface
            assert token.feature.pos1 == expected["pos1"], token.surface
            assert token.feature.pos2 == expected["pos2"], token.surface
            if expected.get("surface_formal"):
                assert token.feature.surface_formal == expected["surface_formal"]

    @pytest.mark.parametrize("row", _corpus(), ids=lambda row: row["note"][:40])
    def test_the_mined_form_matches_the_corpus(self, row, armed):
        policy = PersianMinedForm()
        produced = _content(fa_tokenizer.to_duck_tokens(row["normalized"], armed))
        for token, expected in zip(produced, row["tokens"], strict=True):
            mined = policy.mined_form(token.feature.pos1, token.feature.lemma, token.feature.lemma, token.surface)
            assert mined == expected["mined"], token.surface
            assert "#" not in mined

    @pytest.mark.parametrize("row", _corpus(), ids=lambda row: row["note"][:40])
    def test_every_surface_is_a_slice_of_the_line_in_order(self, row, armed):
        line = row["normalized"]
        cursor = 0
        for token in fa_tokenizer.to_duck_tokens(line, armed):
            found = line.find(token.surface, cursor)
            assert found >= 0, token.surface
            cursor = found + len(token.surface)
        surfaces = "".join(token.surface for token in fa_tokenizer.to_duck_tokens(line, armed))
        assert surfaces.replace(" ", "") == "".join(line.split()).replace(" ", "")


class TestLadder:
    def test_a_compound_surface_is_the_verbatim_span(self, armed):
        line = KAR + " " + MIKONAM
        tokens = fa_tokenizer.to_duck_tokens(line, armed)
        assert len(tokens) == 1
        assert tokens[0].surface == line
        assert " " in tokens[0].surface
        assert tokens[0].feature.lemma == KAR + " " + KARDAN
        assert tokens[0].feature.pos1 == "V"

    def test_a_compound_is_not_a_stopword_even_though_its_verb_is(self, armed):
        # mi-konam is a stopwords.dat entry; the merged span is not.
        assert armed.is_stopword(MIKONAM) is True
        token = fa_tokenizer.to_duck_tokens(KAR + " " + MIKONAM, armed)[0]
        assert token.feature.pos2 == ""

    def test_a_tagged_noun_beats_the_verb_table(self, armed):
        for word in TAGGED_NOUNS:
            token = fa_tokenizer.to_duck_tokens(word, armed)[0]
            assert token.feature.pos1 != "V", word
            assert token.feature.lemma == word, word

    def test_the_verb_table_still_answers_for_forms_words_dat_lacks(self, armed):
        for word in VERB_ONLY:
            token = fa_tokenizer.to_duck_tokens(word, armed)[0]
            assert token.feature.pos1 == "V", word
            assert token.feature.lemma.endswith(_NOON), word

    def test_a_colloquial_stopword_is_still_a_stopword(self, armed):
        # digar is in stopwords.dat and dige is its colloquial spelling.
        dige = _DAL + _YEH + _GAF + _HEH
        digar = _DAL + _YEH + _GAF + _REH
        assert armed.colloquial(dige) == digar
        token = fa_tokenizer.to_duck_tokens(dige, armed)[0]
        assert token.feature.lemma == digar
        assert token.feature.pos2 == "stopword"

    def test_a_verb_carries_its_present_stem(self, armed):
        miravam = MI + ZWNJ + _REH + _WAW + _MEEM
        token = fa_tokenizer.to_duck_tokens(miravam, armed)[0]
        assert token.feature.pos1 == "V"
        assert token.feature.present_stem == _REH + _WAW

    def test_the_morph_string_carries_what_pos2_cannot(self, armed):
        """``morph`` is the only feature channel a TokenizedWord has (see render.py)."""
        miravam = MI + ZWNJ + _REH + _WAW + _MEEM
        assert fa_tokenizer.to_duck_tokens(miravam, armed)[0].morph == "PresentStem=" + _REH + _WAW
        xune = _KHAH + _WAW + _NOON + _HEH
        assert fa_tokenizer.to_duck_tokens(xune, armed)[0].morph == "Register=Informal"
        assert fa_tokenizer.to_duck_tokens(KETAB, armed)[0].morph == ""

    def test_punctuation_and_digits_get_their_own_tiers(self, armed):
        tokens = fa_tokenizer.to_duck_tokens(KETAB + "\N{ARABIC COMMA} 12", armed)
        kinds = {token.surface: token.feature.pos1 for token in tokens}
        assert kinds["\N{ARABIC COMMA}"] == "PUNCT"
        assert kinds["12"] == "NUM"

    def test_an_unknown_word_keeps_its_spelling(self, armed):
        made_up = _ZAIN + _ZAIN + _ZAIN + _ZAIN
        token = fa_tokenizer.to_duck_tokens(made_up, armed)[0]
        assert token.feature.pos1 == "unknown"
        assert token.feature.lemma == made_up

    def test_kana_is_always_empty(self, armed):
        for token in fa_tokenizer.to_duck_tokens(KETAB + " " + MARD, armed):
            assert token.feature.kana == ""


class TestVerbTables:
    """The verb forms spoken and written Persian use that the 79 formal patterns miss."""

    def test_a_formal_present_carries_no_register(self, armed):
        # xar- is kharidan's informal stem AND its formal one; the textbook
        # mi-xaram must not come back as slang with a Colloquial field.
        mixaram = MI + ZWNJ + _KHAH + _REH + _MEEM
        (token,) = fa_tokenizer.to_duck_tokens(mixaram, armed)
        assert token.feature.pos1 == "V"
        assert token.feature.lemma == _KHAH + _REH + _YEH + _DAL + _NOON
        assert token.feature.pos2 == ""
        assert token.feature.surface_formal == ""
        assert token.morph == "PresentStem=" + _KHAH + _REH

    def test_a_colloquial_person_ending_is_an_informal_verb(self, armed):
        mige = MI + _GAF + _HEH
        (token,) = fa_tokenizer.to_duck_tokens(mige, armed)
        assert token.feature.pos1 == "V"
        assert token.feature.lemma == GOFT + _NOON
        assert token.feature.pos2 == "informal"
        assert token.feature.surface_formal == MI + ZWNJ + _GAF + _WAW + _YEH + _DAL

    def test_the_colloquial_word_map_answers_before_the_endings(self, armed):
        # xune is xundan + -e as well; the house wins, as it did before.
        xune = _KHAH + _WAW + _NOON + _HEH
        assert armed.colloquial_ending_verb(xune) is not None
        (token,) = fa_tokenizer.to_duck_tokens(xune, armed)
        assert token.feature.lemma == _KHAH + _ALEF + _NOON + _HEH
        assert token.feature.pos1 == "N"

    def test_an_untagged_colloquial_word_goes_through_the_verb_table(self, armed):
        # colloquial.tsv answers mishe -> mi-shavad, which words.dat does not tag.
        mishe = MI + _SHEEN + _HEH
        formal = MI + ZWNJ + _SHEEN + _WAW + _DAL
        assert armed.colloquial(mishe) == formal
        (token,) = fa_tokenizer.to_duck_tokens(mishe, armed)
        assert token.feature.pos1 == "V"
        assert token.feature.lemma == SHOD + _NOON
        assert token.feature.surface_formal == formal
        assert token.feature.present_stem == _SHEEN + _WAW

    def test_bashe_is_budan_not_bashidan(self, armed):
        bashe = _BEH + _ALEF + _SHEEN + _HEH
        (token,) = fa_tokenizer.to_duck_tokens(bashe, armed)
        assert token.feature.pos1 == "V"
        assert token.feature.lemma == BUD + _NOON

    def test_a_compound_merges_over_a_colloquial_ending(self, armed):
        line = KAR + " " + MI + _KEHEH + _NOON + _HEH
        tokens = fa_tokenizer.to_duck_tokens(line, armed)
        assert len(tokens) == 1
        assert tokens[0].surface == line
        assert tokens[0].feature.lemma == KAR + " " + KARDAN

    def test_a_preverb_present_is_a_verb(self, armed):
        bar_mi_gardam = _BEH + _REH + MI + _GAF + _REH + _DAL + _MEEM
        (token,) = fa_tokenizer.to_duck_tokens(bar_mi_gardam, armed)
        assert token.feature.pos1 == "V"
        assert token.feature.lemma == _BEH + _REH + _GAF + _SHEEN + _TEH + _NOON
        assert token.feature.present_stem == _BEH + _REH + _GAF + _REH + _DAL


class TestTagger:
    def test_the_tagger_is_callable_like_fugashi(self, armed, monkeypatch):
        monkeypatch.setattr(fa_tokenizer, "_load_lexicon", lambda: armed)
        tagger = fa_tokenizer.PersianTagger(armed)
        assert [token.surface for token in tagger(KETAB)] == [KETAB]
        assert [token.surface for token in tagger.parse(KETAB)] == [KETAB]

    def test_building_without_the_pack_names_the_download(self, monkeypatch):
        monkeypatch.setattr("anki_miner.languages.fa.availability.data_root", lambda: None)
        with pytest.raises(ImportError, match="Settings -> Mining Language"):
            fa_tokenizer.build_tagger()

    def test_building_with_the_pack_arms_the_normaliser(self, monkeypatch):
        monkeypatch.setattr("anki_miner.languages.fa.availability.data_root", lambda: FIXTURES)
        monkeypatch.setattr(fa_script, "FA_SEPARATE_MI_HOOK", None)
        tagger = fa_tokenizer.build_tagger()
        assert fa_script.FA_SEPARATE_MI_HOOK is not None
        assert [token.surface for token in tagger.parse(KETAB)] == [KETAB]
        assert fa_tokenizer.active_lexicon() is not None


class TestParser:
    """The real parser over the fixture lexicon: the tagger reads the stored line with its no-break spaces folded."""

    def test_a_compound_across_a_no_break_space_keeps_the_offset_invariant(self, armed, monkeypatch, tmp_path):
        monkeypatch.setitem(tagger_provider._TAGGERS, "fa", LockedTagger(fa_tokenizer.PersianTagger(armed)))
        parser = get_profile("fa").create_parser(switch_language(AnkiMinerConfig(), "fa"))
        line = KAR + NBSP + MIKONAM + "."
        srt = tmp_path / "fa.srt"
        srt.write_text(f"1\n00:00:01,000 --> 00:00:02,000\n{line}\n", encoding="utf-8")

        words, index = parser.parse_subtitle_file_with_index(srt)

        (word,) = words
        assert word.sentence == line
        assert word.sentence[word.surface_start : word.surface_end] == word.surface == KAR + NBSP + MIKONAM
        assert word.mined_form == KAR + " " + KARDAN  # the card front and every lookup key stay folded
        (entry,) = index
        for _key, surface, start, end, _highlight_end in entry.front_spans + entry.lemma_spans:
            assert entry.line_text[start:end] == surface
        (swapped,) = WordFilterService(parser.config).filter_i_plus_one(words, index)
        assert swapped.sentence[swapped.surface_start : swapped.surface_end] == swapped.surface
