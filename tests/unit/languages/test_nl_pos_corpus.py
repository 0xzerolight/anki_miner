"""Dutch mining over self-written sentences through the REAL parser and model (B.8 Stage D fixtures)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.nl.morphology import NL_ALLOWED_POS, NL_EXCLUDED_SUBTYPES
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.languages.tagger_provider import get_tagger
from anki_miner.models.reading import ReadingUnit

CORPUS = Path(__file__).resolve().parents[2] / "fixtures" / "nl" / "pos_corpus.jsonl"
RECORDS = [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines() if line.strip()]
#: Every fine tag nl_core_news_sm put under ADJ/ADV/NOUN/VERB over this corpus (N8). A new one fails here.
EXPECTED_FINE_TAGS = {
    "ADJ|prenom|basis|met-e|stan", "ADJ|prenom|basis|zonder", "ADJ|prenom|comp|met-e|stan", "ADJ|vrij|basis|zonder",
    "BW", "N|soort|ev|basis|gen", "N|soort|ev|basis|genus|stan", "N|soort|ev|basis|onz|stan",
    "N|soort|ev|basis|zijd|stan", "N|soort|ev|dim|onz|stan", "N|soort|mv|basis", "TW|rang|nom|mv-n",
    "TW|rang|nom|zonder-n", "TW|rang|prenom|stan", "VNW|aanw|adv-pron|stan|red|3|getal", "WW|inf|vrij|zonder",
    "WW|pv|tgw|ev", "WW|pv|tgw|met-t", "WW|pv|tgw|mv", "WW|pv|verl|ev", "WW|pv|verl|mv", "WW|vd|vrij|zonder",
}  # fmt: skip


@pytest.fixture(scope="module")
def parser():
    return get_profile("nl").create_parser(switch_language(AnkiMinerConfig(), "nl"))


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
    assert all(word.surface in record["sentence"] for word in words), record["id"]


@pytest.mark.parametrize("record", RECORDS, ids=[record["id"] for record in RECORDS])
def test_tokenizer_surfaces_cover_the_line(record):
    tokens = get_tagger("nl")(record["sentence"])
    assert "".join(token.surface for token in tokens) == record["sentence"].replace(" ", "")


def test_the_excluded_subtypes_pin_matches_real_output():
    seen = {
        token.feature.pos2
        for record in RECORDS
        for token in get_tagger("nl")(record["sentence"])
        if token.feature.pos1 in NL_ALLOWED_POS and token.feature.pos2
    }
    assert seen == EXPECTED_FINE_TAGS
    assert set(NL_EXCLUDED_SUBTYPES) <= seen  # every exclusion is evidenced by this corpus


def test_an_all_caps_cue_bolds_the_original_surface(parser):
    fronts = {word.mined_form: word for word in _words(parser, "DE WINKEL IS GESLOTEN.")}
    assert {"winkel", "sluiten"} <= set(fronts)
    word = fronts["winkel"]
    assert word.surface == "WINKEL"
    assert "DE WINKEL IS GESLOTEN."[word.surface_start : word.surface_end] == "WINKEL"


def test_a_curly_apostrophe_article_never_mines(parser):
    assert "’t" not in {word.mined_form for word in _words(parser, "Hij zei: ’t Is al laat.")}


def test_hyphen_compounds_front_the_hyphenated_spelling(parser):
    fronts = {word.mined_form for word in _words(parser, "Hij had een auto-ongeluk bij het tv-programma.")}
    assert {"auto-ongeluk", "tv-programma"} <= fronts
    assert not {"autoongeluk", "tvprogramma"} & fronts


def test_the_sdh_default_strips_cues_before_tagging(parser):
    words = _words(parser, "JAN: [deur slaat dicht] - Waar is de sleutel?", subtitle_cleanup=True)
    assert "sleutel" in {word.mined_form for word in words}
    assert not {"jan", "Jan", "deur", "slaan", "dicht"} & {word.mined_form for word in words}


def test_the_known_word_front_het_boek_meets_the_mined_boek(parser):
    fold = get_profile("nl").dedup_fold
    assert fold is not None
    (word,) = [w for w in _words(parser, "De student las een boek.") if w.mined_form == "boek"]
    assert fold("het boek") == fold(word.mined_form)
