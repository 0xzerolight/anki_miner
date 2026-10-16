"""service_factory builds the Wikimedia Commons leg from the active profile's prefixes, and only then (F-16)."""

from __future__ import annotations

import dataclasses

from anki_miner.config import AnkiMinerConfig, AudioSourceEntry
from anki_miner.gui.utils import service_factory
from anki_miner.languages.registry import get_profile
from anki_miner.services.expression_audio_fetcher import JPod101AudioFetcher
from anki_miner.services.google_translate_audio_fetcher import GoogleTranslateAudioFetcher
from anki_miner.services.wikimedia_audio_fetcher import WikimediaAudioFetcher
from tests.unit.languages.stub_registry import register_stub_profile

WIKI = AudioSourceEntry(kind="wikimedia")


def _stub(monkeypatch, **audio_overrides) -> None:
    audio = dataclasses.replace(get_profile("ja").audio, **audio_overrides)
    register_stub_profile(monkeypatch, "zh", audio=audio)


def _members(chain: tuple[AudioSourceEntry, ...]) -> list[object]:
    config = dataclasses.replace(AnkiMinerConfig(), language="zh", expression_audio_chain=chain)
    return service_factory.create_expression_audio_fetcher(config)._fetchers


def test_a_profile_with_prefixes_builds_the_commons_leg(monkeypatch, tmp_path):
    _stub(monkeypatch, wikimedia_prefixes=("Zh-",), custom_fetcher_language="zh")
    monkeypatch.setattr(service_factory, "ANKI_MINER_HOME", tmp_path)

    (member,) = _members((WIKI,))

    assert isinstance(member, WikimediaAudioFetcher)
    assert member._prefixes == ("Zh-",)
    assert member._language == "zh"
    assert member._cache_dir == tmp_path / "audio_cache" / "wikimedia"
    assert member._delay == AnkiMinerConfig().expression_audio_delay
    assert not (tmp_path / "audio_cache").exists()  # building touches no disk


def test_no_prefixes_builds_no_leg(monkeypatch):
    _stub(monkeypatch, wikimedia_prefixes=())

    assert _members((WIKI,)) == []


def test_a_disabled_entry_builds_nothing(monkeypatch):
    _stub(monkeypatch, wikimedia_prefixes=("Zh-",))

    assert _members((AudioSourceEntry(kind="wikimedia", enabled=False),)) == []


def test_the_commons_leg_sits_where_the_chain_puts_it(monkeypatch):
    _stub(monkeypatch, wikimedia_prefixes=("Zh-",))

    members = _members((WIKI, AudioSourceEntry(kind="googletts")))

    assert [type(m) for m in members] == [WikimediaAudioFetcher, GoogleTranslateAudioFetcher]


def test_japanese_with_a_hand_added_row_builds_only_jpod101():
    config = dataclasses.replace(AnkiMinerConfig(), expression_audio_chain=(WIKI, AudioSourceEntry(kind="jpod101")))

    members = service_factory.create_expression_audio_fetcher(config)._fetchers

    assert [type(m) for m in members] == [JPod101AudioFetcher]
