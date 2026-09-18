"""Swedish: cp1252 subtitles, a bilingual file's track choice, and YouTube caption codes (B.4)."""

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

FIXTURE = Path(__file__).parents[2] / "fixtures" / "sv" / "dual_audio_ffprobe.json"
SRT = "1\n00:00:01,000 --> 00:00:03,000\nHon köpte en bok på ön, sa hon.\n"


def _write(tmp_path: Path) -> Path:
    path = tmp_path / "sv.srt"
    path.write_bytes(SRT.encode("cp1252"))
    return path


def test_the_japanese_ladder_mangles_the_swedish_a_ring(tmp_path):
    """Negative control, measured: the ja ladder reads the cue as 'Hon köpte en bok pĺ ön, sa hon.'

    cp932 comes first and decodes ö and ä by accident; å (0xE5) is the byte it gets wrong, so the control is that
    ``på`` does not survive -- not that the whole line is unreadable.
    """
    path = _write(tmp_path)
    with pytest.raises(UnicodeDecodeError) as utf8_error:
        path.read_bytes().decode("utf-8")
    events = load_with_fallback_encoding(path, utf8_error.value)
    assert "på" not in events[0].text
    assert "köpte" in events[0].text and "ön" in events[0].text


def test_cp1252_decodes_and_mines_through_the_swedish_parser(tmp_path):
    parser = get_profile("sv").create_parser(switch_language(AnkiMinerConfig(), "sv"))
    words = parser.parse_subtitle_file(_write(tmp_path))
    assert {"köpa", "bok", "ö"} & {word.mined_form for word in words}
    assert all("�" not in word.sentence for word in words)


def test_the_profile_codes_pick_the_swedish_track(tmp_path):
    proc = MagicMock(returncode=0, stdout=FIXTURE.read_text(encoding="utf-8"), stderr="")
    with patch("anki_miner.utils.audio_track_detector.subprocess.run", return_value=proc):
        codes = get_profile("sv").audio_track_codes
        assert find_japanese_audio_stream(tmp_path / "bilingual.mkv", codes=codes).language_tag == "swe"
        japanese = find_japanese_audio_stream(tmp_path / "bilingual.mkv", codes=JAPANESE_LANGUAGE_CODES)
        assert japanese.language_tag == "jpn"


@pytest.mark.parametrize(
    ("automatic", "language", "native"),
    [
        ({"sv": [{}], "sv-orig": [{}]}, "", True),
        ({"sv": [{}]}, "", True),
        ({"sv": [{}]}, "de", False),
        ({"sv": [{}], "en-orig": [{}]}, "", False),
        ({"de": [{}], "de-orig": [{}]}, "", False),
    ],
)
def test_caption_codes_detect_native_swedish(automatic, language, native):
    """The fetcher's real rule (youtube_fetcher._has_native_auto_ja), read with the Swedish captions."""
    data = {"automatic_captions": automatic, "language": language}
    assert YouTubeFetcherService._has_native_auto_ja(data, captions=get_profile("sv").captions) is native


@pytest.mark.parametrize(("language", "present"), [("sv", True), ("sv-SE", True), ("swe", False), ("de", False)])
def test_the_audio_track_pattern_is_anchored(language, present):
    data = {"formats": [{"vcodec": "none", "language": language}]}
    assert YouTubeFetcherService._has_ja_audio_track(data, captions=get_profile("sv").captions) is present
