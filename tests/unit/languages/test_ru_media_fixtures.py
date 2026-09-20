"""Russian: legacy single-byte subtitles, a trilingual file's track choice, and YouTube caption codes."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.services.youtube_fetcher import YouTubeFetcherService
from anki_miner.utils.audio_track_detector import JAPANESE_LANGUAGE_CODES, find_japanese_audio_stream
from anki_miner.utils.subtitle_encoding import load_with_fallback_encoding, script_check_kwarg

FIXTURE = Path(__file__).parents[2] / "fixtures" / "ru" / "dual_audio_ffprobe.json"
LINE = "Студентка читала интересные книги. Ёлка стоит в углу."


def _srt(tmp_path: Path, text: str, codec: str) -> Path:
    path = tmp_path / f"ru-{codec}.srt"
    path.write_bytes(f"1\n00:00:01,000 --> 00:00:03,000\n{text}\n".encode(codec))
    return path


def test_the_bytes_are_not_utf8_and_carry_the_single_byte_letters(tmp_path):
    data = _srt(tmp_path, LINE, "cp1251").read_bytes()
    assert b"\xd1" in data and b"\xa8" in data  # С, Ё (cp1251 puts ё at \xb8 and Ё at \xa8)
    with pytest.raises(UnicodeDecodeError):
        data.decode("utf-8")


def test_the_profile_ladder_decodes_a_cp1251_file(tmp_path):
    path = _srt(tmp_path, LINE, "cp1251")
    with pytest.raises(UnicodeDecodeError) as utf8_error:
        path.read_bytes().decode("utf-8")
    profile = get_profile("ru")
    assert profile.import_encodings == ("utf-8-sig", "cp1251")
    events = load_with_fallback_encoding(
        path,
        utf8_error.value,
        encodings=profile.import_encodings,
        **script_check_kwarg(profile.import_encodings, profile.script),
    )
    assert events[0].text == LINE


def test_a_koi8_r_file_is_a_documented_limit(tmp_path):
    """Plan D9: KOI8-R's letters sit where cp1251's do, so cp1251 decodes the file without raising into
    case-swapped Cyrillic that passes the Cyrillic check; the detector after the ladder never runs."""
    path = _srt(tmp_path, LINE, "koi8_r")
    with pytest.raises(UnicodeDecodeError) as utf8_error:
        path.read_bytes().decode("utf-8")
    profile = get_profile("ru")
    events = load_with_fallback_encoding(
        path,
        utf8_error.value,
        encodings=profile.import_encodings,
        **script_check_kwarg(profile.import_encodings, profile.script),
    )
    assert events[0].text == LINE.encode("koi8_r").decode("cp1251") != LINE
    assert profile.script.contains_target_script(events[0].text)


@pytest.fixture(scope="module")
def parser():
    return get_profile("ru").create_parser(switch_language(AnkiMinerConfig(), "ru"))


def test_a_cp1251_file_decodes_and_mines(parser, tmp_path):
    words = parser.parse_subtitle_file(_srt(tmp_path, LINE, "cp1251"))
    assert {"студентка", "читать", "интересный", "книга", "ёлка"} <= {word.mined_form for word in words}


def test_the_profile_codes_pick_the_russian_track(tmp_path):
    proc = MagicMock(returncode=0, stdout=FIXTURE.read_text(encoding="utf-8"), stderr="")
    with patch("anki_miner.utils.audio_track_detector.subprocess.run", return_value=proc):
        video = tmp_path / "trilingual.mkv"
        assert find_japanese_audio_stream(video, codes=get_profile("ru").audio_track_codes).language_tag == "rus"
        assert find_japanese_audio_stream(video, codes=JAPANESE_LANGUAGE_CODES).language_tag == "jpn"


@pytest.mark.parametrize(
    ("data", "native"),
    [
        ({"automatic_captions": {"ru": [{}], "ru-orig": [{}]}}, True),
        ({"automatic_captions": {"ru": [{}], "en-orig": [{}]}}, False),
        ({"automatic_captions": {"ru": [{}]}, "language": "ru"}, True),
        ({"automatic_captions": {"ru": [{}]}, "language": "uk"}, False),
        ({"automatic_captions": {"ja": [{}], "ja-orig": [{}]}}, False),
    ],
)
def test_caption_codes_detect_native_russian(data, native):
    assert YouTubeFetcherService._has_native_auto_ja(data, captions=get_profile("ru").captions) is native


@pytest.mark.parametrize(("language", "present"), [("ru", True), ("ru-RU", True), ("rus", False), ("uk", False)])
def test_the_audio_track_pattern_is_anchored(language, present):
    data = {"formats": [{"vcodec": "none", "language": language}]}
    assert YouTubeFetcherService._has_ja_audio_track(data, captions=get_profile("ru").captions) is present
