"""ReadingSupport dispatch at the parser emit site (task 1A.9).

``SubtitleParserService`` gains a keyword-only ``reading_support``. ``None`` —
every ja path, since the ja profile's ``_create_parser`` passes nothing — runs
today's JA reading derivation verbatim: furigana assembly, attested-kana
recovery, katakana pronoun folds and the lemma-reading retry, all of which are
JA-shaped end to end.

An injected ``ReadingSupport`` owns the reading fields outright instead. A
non-ja duck token carries ``feature.kana == ""`` by the ``LanguageToken``
contract, so without this seam every zh card would ship a blank reading.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.models import TokenizedWord
from anki_miner.services.subtitle_parser import SubtitleParserService

#: Values the unmodified JA derivation produces for ``_LINE``, captured from the
#: pre-change parser. Byte-identical output is the stage's top constraint.
_LINE = "映画を見た"
_JA_EXPECTED = {
    "映画": ("エイガ", "えいが", "映画[えいが]", "えいが", ""),
    "見る": ("ミ", "みる", "見[み]る", "みる", ""),
}


def _write_srt(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "line.srt"
    path.write_text(f"1\n00:00:01,000 --> 00:00:03,000\n{text}\n\n", encoding="utf-8")
    return path


@pytest.fixture
def config(tmp_path):
    return AnkiMinerConfig(media_temp_folder=tmp_path / "media")


class _StubReading:
    """A ReadingSupport answering one fixed reading, recording every token."""

    def __init__(self, answer: str = "diàn yǐng") -> None:
        self.answer = answer
        self.tokens: list[Any] = []

    def word_reading(self, token: Any) -> str:
        self.tokens.append(token)
        return self.answer


def test_no_reading_support_keeps_the_ja_derivation_byte_identical(config, tmp_path):
    words = SubtitleParserService(config).parse_subtitle_file(_write_srt(tmp_path, _LINE))

    actual = {
        w.mined_form: (
            w.reading,
            w.expression_reading,
            w.expression_furigana,
            w.lemma_reading,
            w.resolved_reading,
        )
        for w in words
    }
    assert actual == _JA_EXPECTED


def test_an_explicit_none_is_the_same_ja_path(config, tmp_path):
    srt = _write_srt(tmp_path, _LINE)
    default = SubtitleParserService(config).parse_subtitle_file(srt)
    explicit = SubtitleParserService(config, reading_support=None).parse_subtitle_file(srt)
    assert [(w.mined_form, w.expression_reading, w.expression_furigana, w.lemma_reading) for w in default] == [
        (w.mined_form, w.expression_reading, w.expression_furigana, w.lemma_reading) for w in explicit
    ]


def test_an_injected_reading_support_owns_the_reading_fields(config, tmp_path):
    stub = _StubReading()
    words = SubtitleParserService(config, reading_support=stub).parse_subtitle_file(_write_srt(tmp_path, _LINE))

    assert words
    for word in words:
        assert word.expression_reading == "diàn yǐng"
        assert word.reading == "diàn yǐng"
        assert word.lemma_reading == "diàn yǐng"
        assert word.expression_furigana == ""
        assert word.resolved_reading == ""


def test_the_support_is_consulted_once_per_emitted_word_with_the_tokenizer_token(config, tmp_path):
    stub = _StubReading()
    words = SubtitleParserService(config, reading_support=stub).parse_subtitle_file(_write_srt(tmp_path, _LINE))

    assert len(stub.tokens) == len(words)
    for token in stub.tokens:
        assert not isinstance(token, TokenizedWord)
        assert hasattr(token, "surface")
        assert hasattr(token, "feature")


def test_the_injected_path_never_runs_the_ja_derivation(config, tmp_path):
    """The JA block is skipped entirely, not merely overwritten."""
    stub = _StubReading(answer="")
    words = SubtitleParserService(config, reading_support=stub).parse_subtitle_file(_write_srt(tmp_path, _LINE))

    assert words
    assert all(w.expression_reading == "" and w.expression_furigana == "" for w in words)
