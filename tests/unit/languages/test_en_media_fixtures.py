"""English: cp1252 subtitles, a bilingual file's track choice, and YouTube caption codes."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.services.youtube_fetcher import YouTubeFetcherService
from anki_miner.utils.audio_track_detector import JAPANESE_LANGUAGE_CODES, find_japanese_audio_stream
from anki_miner.utils.subtitle_encoding import load_with_fallback_encoding

FIXTURE = Path(__file__).parents[2] / "fixtures" / "en" / "dual_audio_ffprobe.json"
SRT = "1\n00:00:01,000 --> 00:00:03,000\nI don’t know where the café is.\n"
#: No é: an 0xE9 byte before a space is invalid cp932, which would send the ja ladder to the detector (I-4).
CONTROL_SRT = "1\n00:00:01,000 --> 00:00:03,000\nI don’t know where it is.\n"


def _write(tmp_path: Path, text: str = SRT) -> Path:
    path = tmp_path / "en.srt"
    path.write_bytes(text.encode("cp1252"))
    return path


def test_the_japanese_ladder_mangles_a_curly_apostrophe(tmp_path):
    """Negative control (memory cp932-swallows-cp1252-apostrophes), reproduced on main: don’t → don稚."""
    path = _write(tmp_path, CONTROL_SRT)
    with pytest.raises(UnicodeDecodeError) as utf8_error:
        path.read_bytes().decode("utf-8")
    events = load_with_fallback_encoding(path, utf8_error.value)
    assert "don稚" in events[0].text


def test_cp1252_decodes_and_mines_through_the_english_parser(tmp_path):
    parser = get_profile("en").create_parser(switch_language(AnkiMinerConfig(), "en"))
    words = parser.parse_subtitle_file(_write(tmp_path))
    fronts = {word.mined_form for word in words}
    assert {"know", "café"} <= fronts
    assert all("�" not in word.sentence and "稚" not in word.sentence for word in words)


def test_the_profile_codes_pick_the_english_track(tmp_path):
    proc = MagicMock(returncode=0, stdout=FIXTURE.read_text(encoding="utf-8"), stderr="")
    with patch("anki_miner.utils.audio_track_detector.subprocess.run", return_value=proc):
        codes = get_profile("en").audio_track_codes
        assert find_japanese_audio_stream(tmp_path / "bilingual.mkv", codes=codes).language_tag == "eng"
        assert (
            find_japanese_audio_stream(tmp_path / "bilingual.mkv", codes=JAPANESE_LANGUAGE_CODES).language_tag == "jpn"
        )


@pytest.mark.parametrize(
    ("automatic", "language", "native"),
    [
        ({"en": [{}], "en-orig": [{}]}, "", True),
        # No -orig key at all proves nothing (its registration is conditional): the language field decides.
        ({"en": [{}]}, "", True),
        ({"en": [{}]}, "en-GB", True),
        ({"en": [{}]}, "de", False),
        # Another language's -orig names a non-English original: the bare en track is a translation.
        ({"en": [{}], "de-orig": [{}]}, "", False),
        # The bare primary track is required; a regional track alone is not mined.
        ({"en-GB": [{}], "en-orig": [{}]}, "", False),
        ({"ja": [{}], "ja-orig": [{}]}, "", False),
    ],
)
def test_caption_codes_detect_native_english(automatic, language, native):
    """The fetcher's real rule (youtube_fetcher._has_native_auto_ja), read with the English captions."""
    captions = get_profile("en").captions
    data = {"automatic_captions": automatic, "language": language}
    assert YouTubeFetcherService._has_native_auto_ja(data, captions=captions) is native


@pytest.mark.parametrize(("language", "present"), [("en", True), ("en-US", True), ("eng", False), ("ja", False)])
def test_the_audio_track_pattern_is_anchored(language, present):
    data = {"formats": [{"vcodec": "none", "language": language}]}
    assert YouTubeFetcherService._has_ja_audio_track(data, captions=get_profile("en").captions) is present
