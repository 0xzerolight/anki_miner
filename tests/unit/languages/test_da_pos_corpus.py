"""Danish mining over self-written sentences through the REAL parser and model (E.9 fixtures)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.da.tokenizer import build_tagger
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.models.reading import ReadingUnit

CORPUS = Path(__file__).resolve().parents[2] / "fixtures" / "da" / "pos_corpus.jsonl"
RECORDS = [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture(scope="module")
def tagger():
    """Built once: the autouse conftest fixture clears the tagger cache around every test."""
    return build_tagger()


@pytest.fixture(scope="module")
def parser():
    return get_profile("da").create_parser(switch_language(AnkiMinerConfig(), "da"))


def _words(parser, sentence: str, **kwargs):
    units = [ReadingUnit(text=sentence, index=0, location_label="t")]
    words, _index, _counts = parser.parse_text_units(units, False, **kwargs)
    return words


@pytest.mark.parametrize("record", RECORDS, ids=[record["id"] for record in RECORDS])
def test_corpus_sentences_mine_the_expected_fronts(parser, record):
    kwargs = {"subtitle_cleanup": True} if record["id"] == "da17" else {}
    words = _words(parser, record["sentence"], **kwargs)
    mined = {word.mined_form for word in words}
    assert set(record["must_mine"]) <= mined, record["id"]
    assert not set(record["must_not_mine"]) & mined, record["id"]


@pytest.mark.parametrize("record", RECORDS, ids=[record["id"] for record in RECORDS])
def test_tokenizer_surfaces_cover_the_line(tagger, record):
    tokens = tagger(record["sentence"])
    assert "".join(token.surface for token in tokens) == record["sentence"].replace(" ", "")


def test_pos2_is_dead_for_the_whole_corpus(tagger):
    """D11: no tagger component, so the fine tag is never more than UPOS and excluded_subtypes gates nothing."""
    assert {token.feature.pos2 for record in RECORDS for token in tagger(record["sentence"])} == {""}
    assert get_profile("da").pos_defaults.excluded_subtypes == ()


@pytest.mark.parametrize(
    ("sentence", "surface", "lemma"),
    [
        ("Huset var stort.", "Huset", "hus"),
        ("Børnene legede i haven.", "Børnene", "barn"),
        ("Gaderne var tomme.", "Gaderne", "gade"),
        # Already right without the seam: variant R's lemma == surface guard leaves them alone (finding 3).
        ("Pigerne løb hjem.", "Pigerne", "pige"),
        ("Husk at låse døren.", "Husk", "huske"),
    ],
)
def test_the_capital_lemma_seam_repairs_only_what_the_model_missed(tagger, sentence, surface, lemma):
    (token,) = [t for t in tagger(sentence) if t.surface == surface]
    assert token.feature.lemma == lemma


def test_the_seam_is_off_by_default_so_the_repair_is_the_profile_s_choice():
    """Negative control: the same sentence keeps the surface as the lemma without the opt-in."""
    from anki_miner.languages._spaced.tokenizer import build_spacy_tagger
    from anki_miner.languages.da.abbreviations import DA_ABBREVIATIONS
    from anki_miner.languages.da.morphology import DA_MODEL_PACKAGE, DA_SEPARABLE_VERB_DEPS

    plain = build_spacy_tagger(
        DA_MODEL_PACKAGE,
        keep_parser=True,
        particle_deps=DA_SEPARABLE_VERB_DEPS,
        abbreviations=DA_ABBREVIATIONS,
    )
    (token,) = [t for t in plain("Huset var stort.") if t.surface == "Huset"]
    assert token.feature.lemma == "huset"


@pytest.mark.parametrize(
    ("surface", "front"),
    [("Katten", "katten"), ("Æbler", "æbl"), ("garagen", "garag"), ("Kom", "Kom")],
)
def test_the_pinned_lemma_misses_are_still_misses(tagger, surface, front):
    """DA3, re-derived from RAW output: kat, aeble, garage and komme are what Danish wants."""
    sentence = {
        "Katten": "Katten sov på bordet.",
        "Æbler": "Æbler er sunde.",
        "garagen": "Han gik ind i garagen.",
        "Kom": "Kom ind!",
    }[surface]
    (token,) = [t for t in tagger(sentence) if t.surface == surface]
    assert token.feature.lemma == front


def test_an_all_caps_cue_bolds_the_original_surface(parser):
    fronts = {word.mined_form: word for word in _words(parser, "BOGEN LIGGER PÅ BORDET.")}
    assert {"bog", "ligge", "bord"} <= set(fronts)
    word = fronts["bog"]
    assert word.surface == "BOGEN"
    assert "BOGEN LIGGER PÅ BORDET."[word.surface_start : word.surface_end] == "BOGEN"


def test_the_probe_verb_lemmatises(parser):
    assert "gå" in {word.mined_form for word in _words(parser, "Han gik hjem.")}


def test_the_known_word_front_en_bog_meets_the_mined_bog(parser):
    fold = get_profile("da").dedup_fold
    assert fold is not None
    (word,) = [w for w in _words(parser, "Den studerende læste en bog.") if w.mined_form == "bog"]
    assert fold("en bog") == fold(word.mined_form)
