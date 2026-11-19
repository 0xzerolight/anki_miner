"""S27: the Google voice a profile asks for reaches the API unchanged.

gTTS 2.5.4 checks ``lang`` against a deprecation table before synthesis and
rewrites ``pt-PT`` to ``pt`` (the Brazilian voice) with only a warning. The
table is identity for every other code a profile uses, so skipping the check
changes nothing else: the codes are profile constants, not user input.
"""

from __future__ import annotations

import dataclasses
import warnings
from unittest.mock import patch

import gtts
import pytest

from anki_miner.config import AnkiMinerConfig, AudioSourceEntry
from anki_miner.gui.utils import service_factory
from anki_miner.languages.switching import switch_language
from anki_miner.services.google_translate_audio_fetcher import GoogleTranslateAudioFetcher
from anki_miner.services.sentence_tts_fetcher import GoogleSentenceTtsFetcher

MODULE = "anki_miner.services.google_translate_audio_fetcher"
_VALID_MP3 = b"ID3" + b"\x00" * 7 + b"\xff\xfb\x90\x00" + b"\x00" * 100


def _recorder():
    calls: list[dict] = []

    class _FakeGTTS:
        def __init__(self, *args, **kwargs):
            calls.append(kwargs)

        def write_to_fp(self, fp):
            fp.write(_VALID_MP3)

    return _FakeGTTS, calls


def test_the_word_fetcher_sends_the_regional_code_unchecked(tmp_path):
    fake, calls = _recorder()
    fetcher = GoogleTranslateAudioFetcher(cache_dir=tmp_path, delay=0, gtts_lang="pt-PT")
    with patch(f"{MODULE}.gtts.gTTS", fake):
        assert fetcher.fetch("食べる", "たべる") is not None
    assert [(call["lang"], call["lang_check"]) for call in calls] == [("pt-PT", False)]


def test_the_sentence_fetcher_shares_the_leaf(tmp_path):
    fake, calls = _recorder()
    fetcher = GoogleSentenceTtsFetcher(cache_dir=tmp_path, delay=0, gtts_lang="pt-PT")
    with patch(f"{MODULE}.gtts.gTTS", fake):
        assert fetcher.fetch("O autocarro chegou atrasado.") is not None
    assert [(call["lang"], call["lang_check"]) for call in calls] == [("pt-PT", False)]


def _pt_google_legs(variant):
    config = dataclasses.replace(
        switch_language(AnkiMinerConfig(), "pt"),
        script_variant=variant,
        expression_audio_chain=(AudioSourceEntry(kind="googletts"),),
        reading_tts_enabled=True,
        reading_tts_google_enabled=True,
    )
    (word,) = [
        f
        for f in service_factory.create_expression_audio_fetcher(config)._fetchers
        if isinstance(f, GoogleTranslateAudioFetcher)
    ]
    (sentence,) = [
        f
        for f in service_factory._build_sentence_audio_fetcher(config)._fetchers
        if isinstance(f, GoogleSentenceTtsFetcher)
    ]
    return word, sentence


def test_a_variety_switch_never_serves_the_other_voices_cache():
    """B6-001 / S1-003: the cache stem (and Anki media name) names the European voice."""
    fake, calls = _recorder()
    br_word, br_sentence = _pt_google_legs("br")
    pt_word, pt_sentence = _pt_google_legs("pt")
    with patch(f"{MODULE}.gtts.gTTS", fake):
        br_paths = (br_word.fetch("ônibus", ""), br_sentence.fetch("O ônibus chegou."))
        assert pt_word.has_cached("ônibus", "") is not True
        pt_paths = (pt_word.fetch("ônibus", ""), pt_sentence.fetch("O ônibus chegou."))
    assert [call["lang"] for call in calls] == ["pt", "pt", "pt-PT", "pt-PT"]
    assert None not in br_paths + pt_paths
    assert br_paths[0] != pt_paths[0] and br_paths[1] != pt_paths[1]
    assert br_paths[0].name.startswith("googletts_pt_")
    assert br_paths[1].name.startswith("sentencetts_pt_google_")


def test_gtts_rewrites_pt_pt_unless_the_check_is_skipped():
    """The upstream behaviour the kwarg exists for (no network: the constructor only)."""
    with pytest.warns(DeprecationWarning):
        assert gtts.gTTS(text="olá", lang="pt-PT").lang == "pt"
    assert gtts.gTTS(text="olá", lang="pt-PT", lang_check=False).lang == "pt-PT"


@pytest.mark.parametrize(
    "code", ["ja", "ko", "zh-CN", "zh-TW", "en", "de", "fr", "es", "it", "nl", "ca", "pt", "pl", "ru", "uk"]
)
def test_every_other_profile_code_is_unchanged_by_the_check(code):
    """Checked or not, these codes reach the API as written (zh-CN/zh-TW warn but map to themselves)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        assert gtts.gTTS(text="x", lang=code).lang == code
