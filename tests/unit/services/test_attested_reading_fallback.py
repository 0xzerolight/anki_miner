"""S24: an injected ReadingSupport that answers "" may take a unique attested dictionary reading.

The harness is ``test_reading_support_dispatch.py``'s: the ja tagger over ``映画を見た`` with a stub
support, so the seam is pinned without any Russian engine. ru turns the flag on in its create_parser;
ja, ko, zh and the stub never do.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui import app as app_module
from anki_miner.services.subtitle_parser import SubtitleParserService

_LINE = "映画を見た"


def _srt(tmp_path: Path) -> Path:
    path = tmp_path / "line.srt"
    path.write_text(f"1\n00:00:01,000 --> 00:00:03,000\n{_LINE}\n\n", encoding="utf-8")
    return path


class _Blank:
    def word_reading(self, token: Any) -> str:
        return ""


class _Fixed:
    def word_reading(self, token: Any) -> str:
        return "own"


class _Lookup:
    def __init__(self, table: dict[str, list[str]]) -> None:
        self.table = table
        self.calls: list[list[str]] = []

    def __call__(self, terms: list[str]) -> dict[str, list[str]]:
        self.calls.append(list(terms))
        return {term: self.table[term] for term in terms if term in self.table}


@pytest.fixture
def config(tmp_path):
    return AnkiMinerConfig(media_temp_folder=tmp_path / "media")


def _fields(words):
    return {
        w.mined_form: (w.reading, w.expression_reading, w.expression_furigana, w.lemma_reading, w.resolved_reading)
        for w in words
    }


def test_a_unique_attested_reading_fills_the_blank_expression_reading(config, tmp_path):
    lookup = _Lookup({"映画": ["えいが"], "見る": ["みる", "みる"]})
    parser = SubtitleParserService(
        config, reading_lookup=lookup, reading_support=_Blank(), attested_reading_fallback=True
    )
    assert _fields(parser.parse_subtitle_file(_srt(tmp_path))) == {
        "映画": ("", "えいが", "", "えいが", ""),
        "見る": ("", "みる", "", "みる", ""),
    }
    assert len(lookup.calls) == 1  # the line-level prefetch answers every word


def test_two_attested_readings_stay_blank(config, tmp_path):
    lookup = _Lookup({"映画": ["えいが", "えいか"]})
    parser = SubtitleParserService(
        config, reading_lookup=lookup, reading_support=_Blank(), attested_reading_fallback=True
    )
    assert _fields(parser.parse_subtitle_file(_srt(tmp_path)))["映画"] == ("", "", "", "", "")


def test_the_flag_is_off_by_default(config, tmp_path):
    lookup = _Lookup({"映画": ["えいが"]})
    parser = SubtitleParserService(config, reading_lookup=lookup, reading_support=_Blank())
    assert all(
        fields == ("", "", "", "", "") for fields in _fields(parser.parse_subtitle_file(_srt(tmp_path))).values()
    )


def test_a_support_that_answers_keeps_its_answer(config, tmp_path):
    lookup = _Lookup({"映画": ["えいが"]})
    parser = SubtitleParserService(
        config, reading_lookup=lookup, reading_support=_Fixed(), attested_reading_fallback=True
    )
    assert _fields(parser.parse_subtitle_file(_srt(tmp_path)))["映画"] == ("own", "own", "", "own", "")


def test_no_lookup_no_fallback(config, tmp_path):
    parser = SubtitleParserService(config, reading_support=_Blank(), attested_reading_fallback=True)
    assert _fields(parser.parse_subtitle_file(_srt(tmp_path)))["映画"] == ("", "", "", "", "")


def test_the_ja_path_ignores_the_flag(config, tmp_path):
    """No ReadingSupport: the JA derivation runs verbatim with or without the flag."""
    lookup = _Lookup({"映画": ["えいが"]})
    srt = _srt(tmp_path)
    plain = _fields(SubtitleParserService(config, reading_lookup=lookup).parse_subtitle_file(srt))
    flagged = _fields(
        SubtitleParserService(config, reading_lookup=lookup, attested_reading_fallback=True).parse_subtitle_file(srt)
    )
    assert plain == flagged


def _smoke_profile(capabilities: frozenset[str]) -> SimpleNamespace:
    word = SimpleNamespace(expression_reading="", mined_form="слово", orth_base="слово")
    parser = SimpleNamespace(parse_text_units=lambda units, _flag: ([word], {}, {}))
    return SimpleNamespace(
        reading=_Blank(),
        capabilities=capabilities,
        smoke_sentence="Слово.",
        create_parser=lambda config: parser,
        lookup=SimpleNamespace(candidates=lambda *args: []),
    )


@pytest.mark.parametrize(
    ("capabilities", "code"),
    [
        (frozenset({"stress_marks"}), 0),
        # he's vocalised headword is read out of the dictionary too, and the smoke home has none.
        (frozenset({"vocalised_reading"}), 0),
        (frozenset({"rtl", "pos_tag"}), 1),
        (frozenset(), 1),
    ],
)
def test_the_bundled_smoke_skips_the_reading_check_only_for_a_dictionary_reading(monkeypatch, capabilities, code):
    import anki_miner.languages.registry as registry
    import anki_miner.languages.switching as switching
    import anki_miner.languages.tagger_provider as tagger_provider

    monkeypatch.setattr(registry, "get_profile", lambda _code: _smoke_profile(capabilities))
    monkeypatch.setattr(switching, "switch_language", lambda config, _code: config)
    monkeypatch.setattr(tagger_provider, "get_tagger", lambda _code: None)
    assert app_module._run_language_bundled_smoke("xx") == code
