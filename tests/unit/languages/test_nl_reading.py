"""Dutch books: S8 abbreviations, „…” dialogue, soft hyphens (S5) and EPUB line wraps (S14)."""

from __future__ import annotations

import dataclasses

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.models.reading import ReadingUnit
from anki_miner.services.reading.epub_source import _line_join, _parse_content, _walk_body
from anki_miner.services.reading.sentence_splitter import split_sentences

RULES = get_profile("nl").sentence_rules


def test_dutch_abbreviations_keep_a_sentence_whole():
    assert split_sentences("Dhr. Jansen komt om drie uur. Hij is laat.", rules=RULES) == [
        "Dhr. Jansen komt om drie uur.",
        "Hij is laat.",
    ]
    assert split_sentences("We kopen fruit, bijv. appels. Daarna gaan we.", rules=RULES) == [
        "We kopen fruit, bijv. appels.",
        "Daarna gaan we.",
    ]


def test_an_ordinary_word_still_ends_its_sentence():
    assert split_sentences("Ik ben er al. Nu gaan we.", rules=RULES) == ["Ik ben er al.", "Nu gaan we."]
    # kon is a spaCy nl exception and the 179th most frequent subtitle word (judge NL-2)
    assert split_sentences("Hij deed wat hij kon. Toen viel hij.", rules=RULES) == [
        "Hij deed wat hij kon.",
        "Toen viel hij.",
    ]


def test_dutch_dialogue_quotes_hold_their_sentences():
    text = "„Ga weg. Nu.” zei hij. Toen liep hij weg."
    assert split_sentences(text, rules=RULES) == ["„Ga weg. Nu.” zei hij.", "Toen liep hij weg."]
    without_opener = dataclasses.replace(RULES, openers=RULES.openers - {"„"})
    assert len(split_sentences(text, rules=without_opener)) == 3


def test_a_soft_hyphen_never_splits_a_mined_word():
    parser = get_profile("nl").create_parser(switch_language(AnkiMinerConfig(), "nl"))
    units = [ReadingUnit(text="Het is ge\u00adwoon koud.", index=0, location_label="t")]
    words, _index, _counts = parser.parse_text_units(units, False)
    assert "gewoon" in {word.mined_form for word in words}
    assert all("\u00ad" not in word.sentence for word in words)


def test_epub_line_wraps_join_with_a_space_for_dutch():
    assert _line_join(RULES) == " "
    body, _is_cover = _parse_content(
        b'<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml"><body>'
        b"<p>Ik ben op zoek\n      naar het huis.</p></body></html>"
    )
    paragraphs, _gaiji = _walk_body(body, line_join=_line_join(RULES))
    assert paragraphs == ["Ik ben op zoek naar het huis."]
