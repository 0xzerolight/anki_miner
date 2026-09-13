"""S1: Korean and Chinese word audio reaches the synthesis leaf; Japanese is unchanged."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from unittest.mock import patch

import pytest

from anki_miner.config import AnkiMinerConfig, AudioSourceEntry
from anki_miner.gui.utils import service_factory
from anki_miner.languages.ko.audio import KO_AUDIO
from anki_miner.languages.zh.audio import ZH_AUDIO
from anki_miner.services.custom_audio_fetcher import CustomAudioFetcher
from anki_miner.services.google_translate_audio_fetcher import GoogleTranslateAudioFetcher

MODULE = "anki_miner.services.google_translate_audio_fetcher"
_VALID_MP3 = b"ID3" + b"\x00" * 7 + b"\xff\xfb\x90\x00" + b"\x00" * 100


def _gtts():
    calls: list[dict] = []

    class _Fake:
        def __init__(self, *args, **kwargs):
            calls.append({"text": args[0] if args else kwargs.get("text"), **kwargs})

        def write_to_fp(self, fp):
            fp.write(_VALID_MP3)

    return _Fake, calls


@pytest.mark.parametrize(
    ("audio", "pair", "spoken", "stem"),
    [
        (KO_AUDIO, ("학생", "학생"), "학생", "googletts_ko_학생_학생.mp3"),
        (ZH_AUDIO, ("银行", "yínháng"), "银行", "googletts_zh_银行_yínháng.mp3"),
    ],
)
def test_ko_and_zh_pairs_reach_the_synthesis_leaf(tmp_path: Path, audio, pair, spoken, stem):
    fake, calls = _gtts()
    fetcher = GoogleTranslateAudioFetcher(
        cache_dir=tmp_path,
        delay=0,
        gtts_lang=audio.gtts_lang,
        cache_stem_prefix=audio.cache_stem_prefix,
        speakable=audio.speakable,
    )
    with patch(f"{MODULE}.gtts.gTTS", fake):
        out = fetcher.fetch(*pair)

    assert out is not None and out.name == stem
    assert calls[0]["text"] == spoken and calls[0]["lang"] == audio.gtts_lang


def test_japanese_default_gate_is_unchanged(tmp_path: Path):
    fake, calls = _gtts()
    fetcher = GoogleTranslateAudioFetcher(cache_dir=tmp_path, delay=0)
    with patch(f"{MODULE}.gtts.gTTS", fake):
        assert fetcher.fetch("食べる", "たべる").name == "googletts_食べる_たべる.mp3"
        assert fetcher.fetch("食べる", "食べる") is None
    assert [call["text"] for call in calls] == ["たべる"]
    assert fetcher.has_cached("食べる", "食べる") is False


def test_a_speakable_returning_nothing_skips_the_word(tmp_path: Path):
    fetcher = GoogleTranslateAudioFetcher(cache_dir=tmp_path, delay=0, speakable=lambda term, reading: None)
    assert fetcher.fetch("x", "y") is None
    assert fetcher.has_cached("x", "y") is False


def test_the_custom_source_uses_speakable_as_its_gate(tmp_path: Path):
    seen: list[str] = []
    fetcher = CustomAudioFetcher(
        url_template="https://audio.invalid/{term}/{reading}",
        kind="custom",
        cache_dir=tmp_path,
        file_prefix="custom_x",
        delay=0,
        language="ko",
        speakable=KO_AUDIO.speakable,
    )
    with patch(
        "anki_miner.services.custom_audio_fetcher.download_audio_to_cache",
        lambda session, url, *a, **k: seen.append(url),
    ):
        fetcher.fetch("학생", "학생")
    assert seen == ["https://audio.invalid/학생/학생"]
    assert (
        CustomAudioFetcher("https://a.invalid/{term}", "custom", tmp_path, "custom_y", 0).fetch("학생", "학생") is None
    )


def test_the_factory_threads_the_profile_speakable():
    config = dataclasses.replace(
        AnkiMinerConfig(), language="ko", expression_audio_chain=(AudioSourceEntry(kind="googletts"),)
    )
    (member,) = service_factory.create_expression_audio_fetcher(config)._fetchers
    assert member._speakable is KO_AUDIO.speakable
