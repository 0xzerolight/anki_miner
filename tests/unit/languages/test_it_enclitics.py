"""Clitic-bearing verbs through the real model: what the lemmatizer does, and what reaches the card front."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.it.morphology import IT_ALLOWED_POS, IT_EXCLUDED_SUBTYPES
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.languages.tagger_provider import get_tagger
from anki_miner.models.reading import ReadingUnit

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "it" / "enclitic_forms.jsonl"
RECORDS = [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]
#: The wty-it-en headwords the fixture recorded: a stub dictionary with exactly the recorded membership.
HEADWORDS = {r["front"] for r in RECORDS if r["front_is_wty_headword"]} | {
    r["surface"].lower() for r in RECORDS if r["surface_is_wty_headword"]
}


def _by_class(name: str) -> list[dict]:
    return [record for record in RECORDS if record["class"] == name]


def _fronts(sentence: str, **parser_kwargs) -> set[str]:
    parser = get_profile("it").create_parser(switch_language(AnkiMinerConfig(), "it"), **parser_kwargs)
    words, _index, _counts = parser.parse_text_units([ReadingUnit(text=sentence, index=0, location_label="t")], False)
    return {word.mined_form for word in words}


@pytest.mark.parametrize("record", RECORDS, ids=[record["id"] for record in RECORDS])
def test_the_model_output_is_pinned(record):
    (token,) = [t for t in get_tagger("it")(record["sentence"]) if t.surface == record["surface"]]
    assert (token.feature.pos1, token.feature.pos2) == (record["pos"], record["tag"])
    mined = token.feature.pos1 in IT_ALLOWED_POS and token.feature.pos2 not in IT_EXCLUDED_SUBTYPES
    assert mined is record["mined"]
    if mined:
        assert token.feature.lemma == record["front"]


@pytest.mark.parametrize("record", _by_class("infinitive_clitic"), ids=lambda r: r["id"])
def test_infinitive_plus_clitic_fronts_the_infinitive(record):
    assert " " in record["model_lemma"] and record["front"] == record["model_lemma"].split()[0]
    assert record["front"] in _fronts(record["sentence"])
    assert record["front"] in _fronts(
        record["sentence"], term_lookup=lambda words: {w for w in words if w in HEADWORDS}
    )


@pytest.mark.parametrize("record", _by_class("surface_lemma"), ids=lambda r: r["id"])
def test_imperative_and_gerund_plus_clitic_front_the_attested_surface(record):
    assert record["front"] == record["surface"].lower() and record["front_is_wty_headword"]
    assert record["front"] in _fronts(
        record["sentence"], term_lookup=lambda words: {w for w in words if w in HEADWORDS}
    )


@pytest.mark.parametrize("record", _by_class("fabricated_lemma"), ids=lambda r: r["id"])
def test_a_fabricated_lemma_yields_to_the_attested_surface(record):
    assert not record["front_is_wty_headword"] and record["surface_is_wty_headword"]
    assert record["front"] in _fronts(record["sentence"])  # no dictionary wired: the model's lemma stands
    with_dictionary = _fronts(record["sentence"], term_lookup=lambda words: {w for w in words if w in HEADWORDS})
    assert record["surface"].lower() in with_dictionary and record["front"] not in with_dictionary


@pytest.mark.parametrize(
    "record", [r for r in RECORDS if r["mined"] and r["front"] != r["surface"].lower()], ids=lambda r: r["id"]
)
def test_the_ladder_tries_the_surface_first_when_the_front_differs(record):
    """The surface channel (D7): `fammi`, `lavarsi` reach the dictionary even when the front is `fammare`, `lavare`."""
    candidates = get_profile("it").lookup.candidates(record["front"], record["surface"], None)
    assert candidates[0] == (record["surface"], 0)


@pytest.mark.parametrize("record", _by_class("propn_sentence_initial"), ids=lambda r: r["id"])
def test_a_sentence_initial_imperative_read_as_a_name_is_not_mined(record):
    assert record["surface"] not in _fronts(record["sentence"]) and record["surface"].lower() not in _fronts(
        record["sentence"]
    )


def test_the_ladder_recovers_an_infinitive_the_dictionary_lacks_as_a_combined_form():
    candidates = [text for text, _ in get_profile("it").lookup.candidates("dimmelo", "dimmelo", None)]
    assert candidates[:2] == ["dim", "dire"]
