"""S1 second half + S27: the Google legs follow the resolved gTTS code."""

from __future__ import annotations

import dataclasses

from anki_miner.config import AnkiMinerConfig, AudioSourceEntry
from anki_miner.gui.utils import service_factory
from anki_miner.languages.registry import get_profile
from anki_miner.services.google_translate_audio_fetcher import GoogleTranslateAudioFetcher
from anki_miner.services.sentence_tts_fetcher import GoogleSentenceTtsFetcher
from tests.unit.languages.stub_registry import register_stub_profile


def _config(**overrides) -> AnkiMinerConfig:
    return dataclasses.replace(
        AnkiMinerConfig(),
        language="zh",
        expression_audio_chain=(AudioSourceEntry(kind="googletts"),),
        reading_tts_enabled=True,
        reading_tts_google_enabled=True,
        **overrides,
    )


def _google_members(config):
    words = service_factory.create_expression_audio_fetcher(config)._fetchers
    sentences = service_factory._build_sentence_audio_fetcher(config)._fetchers
    return (
        [f for f in words if isinstance(f, GoogleTranslateAudioFetcher)],
        [f for f in sentences if isinstance(f, GoogleSentenceTtsFetcher)],
    )


def test_an_empty_code_builds_neither_google_leg(monkeypatch):
    audio = dataclasses.replace(get_profile("ja").audio, gtts_lang="")
    register_stub_profile(monkeypatch, "zh", audio=audio)

    words, sentences = _google_members(_config())

    assert words == [] and sentences == []


def test_a_callable_code_resolves_against_the_config(monkeypatch):
    audio = dataclasses.replace(
        get_profile("ja").audio,
        gtts_lang=lambda config: "zh-TW" if config.script_variant == "traditional" else "zh-CN",
    )
    register_stub_profile(monkeypatch, "zh", audio=audio)

    words, sentences = _google_members(_config(script_variant="traditional"))

    assert [f._gtts_lang for f in words] == ["zh-TW"]
    assert [f._gtts_lang for f in sentences] == ["zh-TW"]


def test_real_profiles_keep_their_plain_codes():
    assert get_profile("ja").audio.resolved_gtts_lang(AnkiMinerConfig()) == "ja"
    assert get_profile("zh").audio.resolved_gtts_lang(AnkiMinerConfig(script_variant="traditional")) == "zh-CN"
