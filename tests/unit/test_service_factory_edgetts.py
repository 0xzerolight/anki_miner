"""service_factory builds the edgetts leg from the active profile's voice, and only then."""

from __future__ import annotations

import dataclasses

from anki_miner.config import AnkiMinerConfig, AudioSourceEntry
from anki_miner.gui.utils import service_factory
from anki_miner.languages.registry import get_profile
from anki_miner.services.edge_tts_audio_fetcher import EdgeTtsAudioFetcher
from anki_miner.services.google_translate_audio_fetcher import GoogleTranslateAudioFetcher
from tests.unit.languages.stub_registry import register_stub_profile

EDGE = AudioSourceEntry(kind="edgetts")


def _speak(term: str, reading: str) -> str | None:
    return term


def _stub(monkeypatch, **audio_overrides) -> None:
    audio = dataclasses.replace(get_profile("ja").audio, **audio_overrides)
    register_stub_profile(monkeypatch, "zh", audio=audio)


def _members(chain: tuple[AudioSourceEntry, ...]) -> list[object]:
    config = dataclasses.replace(AnkiMinerConfig(), language="zh", expression_audio_chain=chain)
    return service_factory.create_expression_audio_fetcher(config)._fetchers


def test_a_profile_voice_builds_the_edge_leg(monkeypatch, tmp_path):
    _stub(monkeypatch, edge_voice="zh-HK-HiuMaanNeural", speakable=_speak)
    monkeypatch.setattr(service_factory, "ANKI_MINER_HOME", tmp_path)

    (member,) = _members((EDGE,))

    assert isinstance(member, EdgeTtsAudioFetcher)
    assert member._voice == "zh-HK-HiuMaanNeural"
    assert member._cache_dir == tmp_path / "audio_cache" / "edgetts"
    assert member._speakable is _speak
    assert member._delay == AnkiMinerConfig().expression_audio_delay
    assert not (tmp_path / "audio_cache").exists()  # building touches no disk


def test_no_voice_builds_no_edge_leg(monkeypatch):
    _stub(monkeypatch)  # the ja audio defaults: edge_voice == ""
    assert _members((EDGE,)) == []


def test_a_disabled_entry_builds_nothing(monkeypatch):
    _stub(monkeypatch, edge_voice="zh-HK-HiuMaanNeural")
    assert _members((AudioSourceEntry(kind="edgetts", enabled=False),)) == []


def test_the_edge_leg_sits_where_the_chain_puts_it(monkeypatch):
    _stub(monkeypatch, edge_voice="he-IL-HilaNeural")
    members = _members((AudioSourceEntry(kind="googletts"), EDGE))
    assert [type(m) for m in members] == [GoogleTranslateAudioFetcher, EdgeTtsAudioFetcher]


def test_without_a_google_voice_the_edge_leg_is_the_only_synthetic_one(monkeypatch):
    # The fa/sl shape, plus a googletts row a user could still carry in (an imported settings profile).
    _stub(monkeypatch, gtts_lang="", edge_voice="sl-SI-PetraNeural", default_chain=(EDGE,))
    members = _members((EDGE, AudioSourceEntry(kind="googletts")))
    assert [type(m) for m in members] == [EdgeTtsAudioFetcher]


def test_the_ja_default_chain_builds_no_edge_leg():
    members = service_factory.create_expression_audio_fetcher(AnkiMinerConfig())._fetchers
    assert not any(isinstance(m, EdgeTtsAudioFetcher) for m in members)
