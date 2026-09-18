"""Finnish books: soft hyphens and NBSP normalised before tagging (S5), and EPUB line wraps join with a space (S14)."""

from __future__ import annotations

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages import tagger_provider
from anki_miner.languages.fi.tokenizer import build_tagger
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.models.reading import ReadingUnit
from anki_miner.services.reading.epub_source import _line_join, _parse_content, _walk_body

RULES = get_profile("fi").sentence_rules


@pytest.fixture(scope="module")
def finnish_tagger():
    return build_tagger()


@pytest.fixture(autouse=True)
def _reuse_the_finnish_tagger(finnish_tagger, monkeypatch):
    monkeypatch.setitem(tagger_provider._TAGGERS, "fi", finnish_tagger)


def _words(text: str):
    parser = get_profile("fi").create_parser(switch_language(AnkiMinerConfig(), "fi"))
    words, _index, _counts = parser.parse_text_units([ReadingUnit(text=text, index=0, location_label="t")], False)
    return words


def test_a_soft_hyphen_never_splits_a_mined_word():
    words = _words("Koira juoksee puis\u00adtossa.")
    assert "puisto" in {word.mined_form for word in words}
    assert all("\u00ad" not in word.sentence and "\u00ad" not in word.mined_form for word in words)


def test_a_no_break_space_becomes_a_space():
    words = _words("Maksoin 10\u00a0000 euroa.")
    assert "euro" in {word.mined_form for word in words}
    assert all("\u00a0" not in word.sentence and "10 000" in word.sentence for word in words)


def test_epub_line_wraps_join_with_a_space_for_finnish():
    assert _line_join(RULES) == " "
    body, _is_cover = _parse_content(
        b'<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml"><body>'
        b"<p>Menin eilen\n      kirjastoon lukemaan.</p></body></html>"
    )
    paragraphs, _gaiji = _walk_body(body, line_join=_line_join(RULES))
    assert paragraphs == ["Menin eilen kirjastoon lukemaan."]
