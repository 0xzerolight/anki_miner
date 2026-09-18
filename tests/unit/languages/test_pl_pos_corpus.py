"""Polish mining over self-written dialogue lines through the REAL parser and model (E.9 fixtures)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.pl.morphology import PL_ALLOWED_POS
from anki_miner.languages.pl.tokenizer import build_tagger
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.models.reading import ReadingUnit

CORPUS = Path(__file__).resolve().parents[2] / "fixtures" / "pl" / "pos_corpus.jsonl"
RECORDS = [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines() if line.strip()]


# Module scope: the parser and the tagger keep their own model reference, so conftest's per-test
# tagger-cache reset does not reload pl_core_news_sm for every record.
@pytest.fixture(scope="module")
def parser():
    return get_profile("pl").create_parser(switch_language(AnkiMinerConfig(), "pl"))


@pytest.fixture(scope="module")
def tagger():
    return build_tagger()


def _words(parser, sentence: str, **kwargs):
    units = [ReadingUnit(text=sentence, index=0, location_label="t")]
    words, _index, _counts = parser.parse_text_units(units, False, **kwargs)
    return words


@pytest.mark.parametrize("record", RECORDS, ids=[record["id"] for record in RECORDS])
def test_corpus_sentences_mine_the_expected_fronts(parser, record):
    words = _words(parser, record["sentence"])
    mined = {word.mined_form for word in words}
    assert set(record["must_mine"]) <= mined, record["id"]
    assert not set(record["must_not_mine"]) & mined, record["id"]
    stored = get_profile("pl").normalize(record["sentence"])
    assert all(word.surface in stored for word in words), record["id"]


@pytest.mark.parametrize("record", RECORDS, ids=[record["id"] for record in RECORDS])
def test_tokenizer_surfaces_cover_the_line(tagger, record):
    tokens = tagger(record["sentence"])
    assert "".join(token.surface for token in tokens) == record["sentence"].replace(" ", "")


def test_pos2_is_live_over_the_corpus(tagger):
    """E.1: pl ships a trained NKJP tagger, so excluded_subtypes is real config (unlike ca).

    ``to_duck_tokens`` records ``tag_`` only when it differs from ``pos_``, and NKJP spells its
    adjective and adverb tags exactly like the UPOS names, so those tokens carry an empty ``pos2``
    while every noun and verb carries a real NKJP tag. Both halves are pinned here: the table in
    ``PL_EXCLUDED_SUBTYPES`` only ever matches the second kind.
    """
    tokens = [token for record in RECORDS for token in tagger(record["sentence"])]
    content = [token for token in tokens if token.feature.pos1 in PL_ALLOWED_POS]
    assert content
    verbal = {token.feature.pos2 for token in content if token.feature.pos1 in {"NOUN", "VERB"}}
    assert {"SUBST", "FIN", "GER", "INF", "PRAET"} <= verbal and "" not in verbal
    assert all(token.feature.pos2 != token.feature.pos1 for token in content)


def test_an_agglutinated_past_tense_mines_the_verb_alone(parser):
    """P2: the trained lemmatiser writes ``widzieć być``; the front is the verb."""
    (word,) = [w for w in _words(parser, "Wczoraj widziałem go w kinie.") if w.pos == "VERB"]
    assert (word.surface, word.mined_form) == ("widziałem", "widzieć")


def test_a_pluralia_tantum_noun_carries_no_gender(parser):
    """P3: UD PDB gives ``drzwi`` Gender=Neut by convention; Polish has none for it."""
    (word,) = [w for w in _words(parser, "Drzwi były otwarte.") if w.mined_form == "drzwi"]
    assert "Gender=" not in word.morph and "Number=Ptan" in word.morph


def test_an_all_caps_cue_bolds_the_original_surface(parser):
    words = [w for w in _words(parser, "KONIEC FILMU") if w.mined_form == "koniec"]
    assert [(w.surface, w.pos) for w in words] == [("KONIEC", "NOUN")]


def test_the_sdh_default_strips_a_polish_speaker_label_before_tagging(parser):
    """P19: the shared Latin capital class cannot match Ł, so pl ships PL_SUBTITLE_REGEX."""
    words = _words(parser, "ŁUKASZ: [dzwonek] Otwórz drzwi!", subtitle_cleanup=True)
    assert {"drzwi"} <= {word.mined_form for word in words}
    assert not {"ŁUKASZ", "łukasz", "dzwonek"} & {word.mined_form for word in words}


def test_a_dotted_abbreviation_is_not_mined(parser):
    """P5: the tokenizer keeps ``godz.``/``ul.`` whole, so the shared rule tags them X."""
    mined = {word.mined_form for word in _words(parser, "Spotkanie jest o godz. 18 przy ul. Długiej.")}
    assert "spotkanie" in mined and not {"godz", "ul", "godzina", "ulica"} & mined


def test_a_deck_front_with_sie_meets_the_mined_verb(parser):
    """P6/S3: Polish decks front a reflexive verb as ``bać się``; the mined front is the verb."""
    fold = get_profile("pl").dedup_fold
    assert fold is not None
    (uczyc,) = [w for w in _words(parser, "Musimy się uczyć codziennie.") if w.mined_form == "uczyć"]
    assert fold("uczyć się") == fold(uczyc.mined_form) == "uczyć"
