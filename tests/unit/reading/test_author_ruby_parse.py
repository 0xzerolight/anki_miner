"""SK-31: an EPUB's own furigana sets the card reading, through the language profile.

Runs the real Japanese parser (fugashi + unidic-lite, as
``test_subtitle_parser_text_units.py`` does). 今日 parses as きょう; an author
who prints こんにち over it means the literary reading.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from unittest.mock import patch

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.models.reading import ReadingUnit
from anki_miner.services.reading.epub_source import load
from anki_miner.services.subtitle_parser import SubtitleParserService
from tests.unit.reading.test_epub_source import _build_epub, _opf, _ref, _xhtml


@pytest.mark.parametrize(("code", "takes"), [("ja", True), ("es", False), ("zh", False), ("ko", False)])
def test_only_a_furigana_profile_takes_author_ruby(code: str, takes: bool) -> None:
    assert get_profile(code).takes_author_ruby is takes


_SENTENCE = "今日は良い天気だ。"


def _parse(tmp_path: Path, units: list[ReadingUnit]):
    service = SubtitleParserService(AnkiMinerConfig(media_temp_folder=tmp_path / "media"))
    words, _index, _counts = service.parse_text_units(units, want_line_index=False)
    return {word.surface: word for word in words}


def test_the_author_reading_replaces_the_tokenizer_reading(tmp_path: Path) -> None:
    words = _parse(
        tmp_path, [ReadingUnit(text=_SENTENCE, index=0, location_label="ch.1", ruby=(("今日", "こんにち"),))]
    )

    kyou = words["今日"]
    assert (kyou.expression_reading, kyou.expression_furigana, kyou.reading, kyou.lemma_reading) == (
        "こんにち",
        "今日[こんにち]",
        "コンニチ",
        "こんにち",
    )
    assert words["天気"].expression_reading == "てんき"


def test_a_unit_without_ruby_keeps_the_tokenizer_reading(tmp_path: Path) -> None:
    words = _parse(tmp_path, [ReadingUnit(text=_SENTENCE, index=0, location_label="ch.1")])

    assert words["今日"].expression_reading == "きょう"


def test_a_profile_without_furigana_ignores_author_ruby(tmp_path: Path) -> None:
    """Review Focus 3: the hint dispatches through the profile, never a language code."""
    plain = dataclasses.replace(get_profile("ja"), capabilities=frozenset())
    service = SubtitleParserService(AnkiMinerConfig(media_temp_folder=tmp_path / "media"))
    unit = ReadingUnit(text=_SENTENCE, index=0, location_label="ch.1", ruby=(("今日", "こんにち"),))

    with patch("anki_miner.languages.registry.get_profile", return_value=plain):
        words, _index, _counts = service.parse_text_units([unit], want_line_index=False)

    assert {w.surface: w.expression_reading for w in words}["今日"] == "きょう"


def test_an_epub_with_ruby_mines_the_authors_reading_end_to_end(tmp_path: Path) -> None:
    files = {
        "OEBPS/content.opf": _opf([("c1", "ch1.xhtml", "application/xhtml+xml", "")], [("c1", None)]),
        "OEBPS/ch1.xhtml": _xhtml("<p><ruby>今日<rt>こんにち</rt></ruby>は良い天気だ。</p>"),
    }
    doc = load(_ref(_build_epub(tmp_path, files)), rules=get_profile("ja").sentence_rules)

    words = _parse(tmp_path, doc.units)

    assert words["今日"].expression_reading == "こんにち"
