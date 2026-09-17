"""R27: the profile code is nb, every downstream service id is no (or both) — proven through the consuming code."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.config.config import AudioSourceEntry
from anki_miner.gui.utils import service_factory
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.services.custom_audio_fetcher import CustomAudioFetcher
from anki_miner.services.google_translate_audio_fetcher import GoogleTranslateAudioFetcher
from anki_miner.services.sentence_tts_fetcher import GoogleSentenceTtsFetcher
from anki_miner.services.youtube_fetcher import YouTubeFetcherService
from anki_miner.utils.audio_track_detector import (
    JAPANESE_LANGUAGE_CODES,
    find_japanese_audio_stream,
    matches_language_tag,
)

FFPROBE = Path(__file__).parents[2] / "fixtures" / "nb" / "dual_audio_ffprobe.json"


@pytest.fixture
def nb_config(test_config):
    return switch_language(test_config, "nb")


def test_google_tts_knows_no_and_not_nb():
    from gtts.lang import tts_langs

    langs = tts_langs()
    assert langs["no"] == "Norwegian" and "nb" not in langs


def test_both_google_legs_speak_no_under_nb_stems():
    config = dataclasses.replace(
        switch_language(AnkiMinerConfig(), "nb"),
        expression_audio_chain=(
            AudioSourceEntry(kind="googletts"),
            AudioSourceEntry(kind="custom", url="http://127.0.0.1:5050/?term={term}&language={language}"),
        ),
        reading_tts_enabled=True,
        reading_tts_google_enabled=True,
    )
    words = service_factory.create_expression_audio_fetcher(config)._fetchers
    sentences = service_factory._build_sentence_audio_fetcher(config)._fetchers
    (google,) = [f for f in words if isinstance(f, GoogleTranslateAudioFetcher)]
    (custom,) = [f for f in words if isinstance(f, CustomAudioFetcher)]
    (sentence,) = [f for f in sentences if isinstance(f, GoogleSentenceTtsFetcher)]
    assert (google._gtts_lang, google._cache_stem_prefix) == ("no", "googletts_nb")
    assert (sentence._gtts_lang, sentence._cache_stem_prefix) == ("no", "sentencetts_nb")
    assert custom._language == "nb"


def test_asr_asks_whisper_for_no():
    assert get_profile("nb").asr_language == "no"


def test_the_frequency_catalogue_reads_the_no_folder():
    (frequency,) = [spec for spec in get_profile("nb").catalog if spec.kind == "freq"]
    assert frequency.id == "opensubtitles-no"
    assert frequency.url.endswith("/content/2018/no/no_50k.txt")


def test_the_download_tab_asks_yt_dlp_for_both_codes(nb_config):
    assert nb_config.downloader_subtitle_langs == "no,nb"


@pytest.mark.parametrize(
    ("automatic", "language", "native"),
    [
        ({"no": [{}], "no-orig": [{}]}, "", True),
        ({"no": [{}]}, "nb", True),
        ({"no": [{}]}, "no", True),
        # Nynorsk and Swedish are other languages; another -orig names a non-Norwegian original
        ({"no": [{}]}, "nn", False),
        ({"no": [{}], "sv-orig": [{}]}, "", False),
        # the probe key is no; a bare nb auto track alone is not probed (R27 primary).
        # This row pins the CODE PATH, not a claim about YouTube's real key — see the
        # "single unverified R27 id" note in the plan (B14 addendum).
        ({"nb": [{}]}, "", False),
    ],
)
def test_caption_codes_detect_native_norwegian(automatic, language, native):
    data = {"automatic_captions": automatic, "language": language}
    assert YouTubeFetcherService._has_native_auto_ja(data, captions=get_profile("nb").captions) is native


@pytest.mark.parametrize(
    ("language", "present"), [("no", True), ("nb", True), ("nb-NO", True), ("nn", False), ("nor", False), ("sv", False)]
)
def test_the_audio_track_pattern_takes_no_and_nb_only(language, present):
    data = {"formats": [{"vcodec": "none", "language": language}]}
    assert YouTubeFetcherService._has_ja_audio_track(data, captions=get_profile("nb").captions) is present


def test_fetch_cmd_asks_for_no_captions_and_a_norwegian_dub(nb_config, tmp_path):
    cmd = YouTubeFetcherService(nb_config)._build_fetch_cmd("https://y", tmp_path, "auto_dub", fallback_allowed=False)
    assert cmd[cmd.index("--sub-lang") + 1] == "no"
    assert cmd[cmd.index("--format") + 1].endswith("+bestaudio[language~='^n[ob](-|$)']")


@pytest.mark.parametrize("name", ["abc123.no.srt", "abc123.nb.vtt"])
def test_resolved_outputs_accept_either_code(nb_config, tmp_path, name):
    (tmp_path / "abc123.mp4").write_bytes(b"v")
    (tmp_path / name).write_bytes(b"1\n00:00:01,000 --> 00:00:02,000\nhei\n")
    media = YouTubeFetcherService(nb_config)._resolve_outputs(tmp_path, "abc123", "auto_only")
    assert media.subtitle_file.name == name


def test_track_codes_take_bokmal_and_macrolanguage_tags_never_nynorsk(tmp_path):
    codes = get_profile("nb").audio_track_codes
    assert codes == frozenset({"nor", "nob", "no", "nb", "norwegian"})
    assert all(matches_language_tag(tag, codes) for tag in ("nor", "nob", "no", "nb-NO", "Norwegian"))
    assert not any(matches_language_tag(tag, codes) for tag in ("nno", "nn", "swe", "dan"))
    proc = MagicMock(returncode=0, stdout=FFPROBE.read_text(encoding="utf-8"), stderr="")
    with patch("anki_miner.utils.audio_track_detector.subprocess.run", return_value=proc):
        assert find_japanese_audio_stream(tmp_path / "dub.mkv", codes=codes).language_tag == "nor"
        assert find_japanese_audio_stream(tmp_path / "dub.mkv", codes=JAPANESE_LANGUAGE_CODES).language_tag == "jpn"


def test_app_side_keys_stay_on_the_profile_code(nb_config):
    assert service_factory.resolve_known_words_db_path(nb_config).name == "known_words.nb.db"
    audio = get_profile("nb").audio
    assert (audio.cache_stem_prefix, audio.sentence_cache_stem_prefix, audio.custom_fetcher_language) == (
        "googletts_nb",
        "sentencetts_nb",
        "nb",
    )
