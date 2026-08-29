"""Card Backfill builds its word-audio ladder from the language profile.

The mining path got this in 1B.9 (``create_expression_audio_fetcher`` hands
``AudioDefaults.candidates`` to ``ChainedExpressionAudioFetcher``); backfill
built its query pairs through the Japanese ladder unconditionally, so a zh scan
either missed everything or voiced a kana guess. ``candidates=None`` (ja) must
keep the old ladder byte-for-byte.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from anki_miner.languages.zh.audio import zh_audio_candidates
from anki_miner.services import card_backfiller
from anki_miner.services.card_backfiller import BackfillOptions, scan_backfill
from anki_miner.services.morphology import SyntheticToken


class FakeAnkiService:
    """Minimal AnkiConnect stand-in: canned note type, fields and notes."""

    def __init__(self, notes: dict[int, dict], note_fields: list[str]):
        self.notes = notes
        self.note_fields = note_fields

    def note_type_names(self) -> list[str]:
        return ["test_note_type"]

    def note_type_field_names(self, note_type: str) -> set[str]:
        return set(self.note_fields)

    def ordered_note_type_field_names(self, note_type: str) -> list[str]:
        return list(self.note_fields)

    def find_notes(self, query: str) -> list[int]:
        return sorted(self.notes)

    def notes_info(self, note_ids: list[int]) -> list[dict]:
        return [self.notes.get(nid, {}) for nid in note_ids]


class FakeAudioFetcher:
    """Records the ladder it is handed; hits on any candidate term in ``hits``."""

    def __init__(self, hits: dict[str, Path] | None = None):
        self.hits = hits or {}
        self.calls: list[list[tuple[str, str]]] = []

    def fetch_candidates(self, candidates, cancelled_check=None):
        self.calls.append(list(candidates))
        for term, _reading in candidates:
            if term in self.hits:
                return self.hits[term]
        return None


def _note(note_id: int, **field_values: str) -> dict:
    return {
        "noteId": note_id,
        "fields": {name: {"value": value} for name, value in field_values.items()},
    }


def _services():
    return SimpleNamespace(
        pitch_accent_service=None,
        frequency_service=None,
        definition_service=None,
    )


_AUDIO_ONLY = BackfillOptions(field_keys=frozenset({"expression_audio"}), deck=None, overwrite=False)

_NOTE_FIELDS = ["word", "Reading", "WordAudio"]


def _config(test_config, language: str):
    return replace(
        test_config,
        language=language,
        anki_fields={
            **test_config.anki_fields,
            "word": "word",
            "expression_reading": "Reading",
            "expression_audio": "WordAudio",
        },
    )


@pytest.fixture
def ja_config(test_config):
    return _config(test_config, "ja")


@pytest.fixture
def zh_config(test_config):
    return _config(test_config, "zh")


@pytest.fixture
def ja_tagger(monkeypatch):
    """Deterministic single-token ja tagger (no MeCab, no UniDic)."""
    kana = {"猫": "ネコ"}

    def fake_tagger(text):
        return [SyntheticToken(text, "名詞", "*", text, kana.get(text, text))]

    monkeypatch.setattr("anki_miner.services.card_backfiller.get_shared_tagger", lambda: fake_tagger)
    return fake_tagger


@pytest.fixture
def no_tagger(monkeypatch):
    """No tokenizer at all for the non-ja scan: kana logic cannot contribute."""
    monkeypatch.setattr("anki_miner.services.card_backfiller.get_tagger", lambda language: None)


@pytest.fixture
def ja_ladder_spy(monkeypatch):
    """Records every ja-ladder entry point the scan reaches."""
    calls: list[tuple] = []
    real = card_backfiller.word_audio_candidates

    def spy(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr("anki_miner.services.card_backfiller.word_audio_candidates", spy)

    def reading_spy(text, tagger):  # pragma: no cover - a call is the failure
        calls.append((text, tagger))
        return ""

    monkeypatch.setattr("anki_miner.services.card_backfiller.generate_reading", reading_spy)
    return calls


def test_zh_scan_uses_the_zh_ladder(zh_config, no_tagger, ja_ladder_spy, tmp_path):
    """The fetcher gets exactly what ``zh_audio_candidates`` produces."""
    mp3 = tmp_path / "googletts_zh_电脑.mp3"
    mp3.write_bytes(b"ID3")
    anki = FakeAnkiService(
        {1: _note(1, word="电脑", Reading="diàn nǎo", WordAudio="")},
        _NOTE_FIELDS,
    )
    fetcher = FakeAudioFetcher({"电脑": mp3})

    plan = scan_backfill(anki, zh_config, _services(), _AUDIO_ONLY, expression_audio_fetcher=fetcher)

    expected = zh_audio_candidates(SimpleNamespace(mined_form="电脑", expression_reading="diàn nǎo"))
    assert fetcher.calls == [expected]
    assert expected[0] == ("电脑", "diàn nǎo")
    # The ja ladder is never consulted: no kana logic runs on a Han front.
    assert ja_ladder_spy == []
    (change,) = plan.notes[0].changes
    assert change.new_value == f"[sound:{mp3.name}]"


def test_ja_scan_keeps_todays_ladder(ja_config, ja_tagger, tmp_path):
    """ja resolves ``candidates=None`` to the pre-existing ladder, unchanged."""
    mp3 = tmp_path / "jpod101_猫_ねこ.mp3"
    mp3.write_bytes(b"ID3")
    anki = FakeAnkiService(
        {1: _note(1, word="猫", Reading="ねこ", WordAudio="")},
        _NOTE_FIELDS,
    )
    fetcher = FakeAudioFetcher({"猫": mp3})

    plan = scan_backfill(anki, ja_config, _services(), _AUDIO_ONLY, expression_audio_fetcher=fetcher)

    assert fetcher.calls == [[("猫", "ねこ")]]
    (change,) = plan.notes[0].changes
    assert change.new_value == f"[sound:{mp3.name}]"


def test_empty_profile_ladder_fetches_nothing(zh_config, no_tagger, monkeypatch):
    """A profile ladder that yields no pair costs no request and proposes nothing."""
    profile = SimpleNamespace(audio=SimpleNamespace(candidates=lambda word: []))
    monkeypatch.setattr("anki_miner.services.card_backfiller.get_profile", lambda code: profile)
    anki = FakeAnkiService(
        {1: _note(1, word="电脑", Reading="diàn nǎo", WordAudio="")},
        _NOTE_FIELDS,
    )
    fetcher = FakeAudioFetcher({"电脑": Path("unused.mp3")})

    plan = scan_backfill(anki, zh_config, _services(), _AUDIO_ONLY, expression_audio_fetcher=fetcher)

    assert fetcher.calls == []
    assert plan.notes == ()
