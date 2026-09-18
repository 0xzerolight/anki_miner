"""Croatian mining over real sentences through the REAL parser and model (E.9 fixtures).

The short-infinitive rows need a dictionary: the repair only fires when the long form is attested, so the
parser is built with a ``term_lookup`` over the committed fixture headwords - the same rows the dictionary
suite imports.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.languages.tagger_provider import get_tagger
from anki_miner.models.reading import ReadingUnit

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "hr"
RECORDS = [
    json.loads(line)
    for line in (FIXTURES / "pos_corpus.jsonl").read_text(encoding="utf-8").splitlines()
    if line.strip()
]
#: Real wty-sh-en headwords, from the two committed fixtures: what the dictionary would attest.
HEADWORDS = frozenset(
    {row[0] for row in json.loads((FIXTURES / "wty_row.json").read_text(encoding="utf-8"))["term_rows"]}
    | {row[0] for row in json.loads((FIXTURES / "tag_bank_sample.json").read_text(encoding="utf-8"))["term_rows"]}
)


def _term_lookup(words: list[str]) -> set[str]:
    return {word for word in words if word in HEADWORDS}


# Module scope: conftest's per-test tagger-cache reset would otherwise reload hr_core_news_sm per record.
@pytest.fixture(scope="module")
def parser():
    config = switch_language(AnkiMinerConfig(), "hr")
    return get_profile("hr").create_parser(config, term_lookup=_term_lookup)


@pytest.fixture(scope="module")
def tagger():
    return get_tagger("hr")


def _words(parser, sentence: str, **kwargs):
    units = [ReadingUnit(text=sentence, index=0, location_label="t")]
    words, _index, _counts = parser.parse_text_units(units, False, **kwargs)
    return words


def _normalized(sentence: str) -> str:
    return get_profile("hr").normalize(sentence)


@pytest.mark.parametrize("record", RECORDS, ids=[record["id"] for record in RECORDS])
def test_corpus_sentences_mine_the_expected_fronts(parser, record):
    mined = {word.mined_form for word in _words(parser, record["sentence"])}
    assert set(record["must_mine"]) <= mined, record["id"]
    assert not set(record["must_not_mine"]) & mined, record["id"]


@pytest.mark.parametrize("record", RECORDS, ids=[record["id"] for record in RECORDS])
def test_tokenizer_surfaces_cover_the_line(tagger, record):
    line = _normalized(record["sentence"])
    assert "".join(token.surface for token in tagger(line)) == line.replace(" ", "")


def test_the_short_infinitive_repair_needs_the_dictionary(parser):
    """Without a term_lookup the model's own answer stands: the card fronts the short form."""
    bare = get_profile("hr").create_parser(switch_language(AnkiMinerConfig(), "hr"))
    assert "morati" in {word.mined_form for word in _words(parser, "Morat \u0107u i\u0107i ku\u0107i.")}
    assert "morat" in {word.mined_form for word in _words(bare, "Morat \u0107u i\u0107i ku\u0107i.")}


def test_the_dje_letter_survives_the_whole_pipeline(parser):
    words = _words(parser, "Gra\u0111anin je kupio \u0111a\u010dki priru\u010dnik.")
    assert "gra\u0111anin" in {word.mined_form for word in words}
    assert all("\u0111" in word.sentence for word in words if "gra\u0111" in word.mined_form)


def test_a_cyrillic_line_mines_nothing(parser):
    """The Latin gate: the sh dictionary carries Cyrillic spellings, the Croatian card never does."""
    assert (
        _words(parser, "\u041a\u045a\u0438\u0433\u0435 \u0441\u0443 \u043d\u0430 \u0441\u0442\u043e\u043b\u0443.") == []
    )


def test_the_fine_tag_table_keeps_numerals_and_abbreviations_off_the_card(parser):
    mined = {word.mined_form for word in _words(parser, "Ro\u0111en je 5. svibnja 1990. godine.")}
    assert {"svibanj", "godina"} <= mined and not {"5", "1990"} & mined


def test_the_sdh_default_strips_a_croatian_speaker_label(parser):
    """The shared Latin speaker rule cannot start on \u017d/\u010c/\u0106/\u0160/\u0110, so hr carries its own."""
    units = [
        ReadingUnit(text="\u017dELJKO: [vrata se zatvaraju] - Knjiga je na stolu. - Da.", index=0, location_label="t")
    ]
    words, _index, _counts = parser.parse_text_units(units, False, subtitle_cleanup=True)
    mined = {word.mined_form for word in words}
    assert "knjiga" in mined and not {"\u017deljko", "vrata", "-knjiga"} & mined
    # Real output: the label and the bracket are gone, and so is the dash that follows a full stop. The
    # first dash stays - once the bracket is stripped it sits mid-line, which the shared rule leaves alone.
    assert {word.sentence for word in words} == {"- Knjiga je na stolu. Da."}
