"""Audio cache stems are language-namespaced; ja stems stay byte-identical."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from unittest.mock import patch

from anki_miner.config import AnkiMinerConfig, AudioSourceEntry
from anki_miner.services.custom_audio_fetcher import _substitute_custom_url
from anki_miner.services.google_translate_audio_fetcher import GoogleTranslateAudioFetcher
from anki_miner.services.sentence_tts_fetcher import (
    PAPAGO_SPEAKER_JA,
    GoogleSentenceTtsFetcher,
    PapagoSentenceTtsFetcher,
    _sentence_stem,
)

MODULE = "anki_miner.services.google_translate_audio_fetcher"
# Same body shape the existing suite uses (ID3 header + a frame, passes the sniff).
_VALID_MP3 = b"ID3" + b"\x00" * 7 + b"\xff\xfb\x90\x00" + b"\x00" * 100


def _gtts_stub():
    calls: list[dict] = []

    class _FakeGTTS:
        def __init__(self, *args, **kwargs):
            calls.append(kwargs)

        def write_to_fp(self, fp):
            fp.write(_VALID_MP3)

    return _FakeGTTS, calls


def test_ja_word_stem_and_lang_are_unchanged(tmp_path: Path):
    fake, calls = _gtts_stub()
    fetcher = GoogleTranslateAudioFetcher(cache_dir=tmp_path, delay=0)
    with patch(f"{MODULE}.gtts.gTTS", fake):
        out = fetcher.fetch("学生", "がくせい")
    assert out is not None and out.name == "googletts_学生_がくせい.mp3"
    assert calls[0]["lang"] == "ja"


def test_non_ja_word_stem_is_namespaced(tmp_path: Path):
    fake, calls = _gtts_stub()
    fetcher = GoogleTranslateAudioFetcher(
        cache_dir=tmp_path, delay=0, gtts_lang="zh-CN", cache_stem_prefix="googletts_zh"
    )
    # A kana reading keeps the ja-shaped input guard out of the way; the guard
    # itself is Task 1B.9's problem, not this one's.
    with patch(f"{MODULE}.gtts.gTTS", fake):
        out = fetcher.fetch("学生", "がくせい")
    assert out is not None and out.name == "googletts_zh_学生_がくせい.mp3"
    assert calls[0]["lang"] == "zh-CN"


def test_sentence_stem_prefix_defaults_and_namespaces():
    assert _sentence_stem("google", "これは。").startswith("sentencetts_google_")
    assert _sentence_stem("google", "这是。", prefix="sentencetts_zh").startswith("sentencetts_zh_google_")


def test_sentence_fetchers_take_the_prefix_and_speaker(tmp_path: Path):
    google = GoogleSentenceTtsFetcher(cache_dir=tmp_path, delay=0, cache_stem_prefix="sentencetts_ko")
    papago = PapagoSentenceTtsFetcher(cache_dir=tmp_path, delay=0, speaker="kyuri", cache_stem_prefix="sentencetts_ko")
    assert google._cache_stem_prefix == "sentencetts_ko"
    assert papago._cache_stem_prefix == "sentencetts_ko"
    assert papago._speaker == "kyuri"
    assert PapagoSentenceTtsFetcher(cache_dir=tmp_path, delay=0)._speaker == PAPAGO_SPEAKER_JA


def test_custom_url_language_placeholder():
    assert _substitute_custom_url("https://x/{language}/{term}", "学生", "", "zh") == "https://x/zh/学生"


def test_factory_gives_the_ja_defaults():
    from anki_miner.gui.utils import service_factory

    config = dataclasses.replace(
        AnkiMinerConfig(),
        expression_audio_chain=(AudioSourceEntry(kind="googletts"),),
        reading_tts_enabled=True,
    )
    chain = service_factory.create_expression_audio_fetcher(config)
    gtts_members = [f for f in chain._fetchers if isinstance(f, GoogleTranslateAudioFetcher)]
    assert gtts_members, "the googletts chain entry must build a fetcher"
    for member in gtts_members:
        assert member._cache_stem_prefix == "googletts"
        assert member._gtts_lang == "ja"

    sentence = service_factory._build_sentence_audio_fetcher(config)
    for member in sentence._fetchers:
        assert member._cache_stem_prefix == "sentencetts"
