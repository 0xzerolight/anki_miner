"""Catalan: cp1252 subtitles, a trilingual file's track choice, and YouTube caption codes."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.services.youtube_fetcher import YouTubeFetcherService
from anki_miner.utils.audio_track_detector import JAPANESE_LANGUAGE_CODES, find_japanese_audio_stream

FIXTURE = Path(__file__).parents[2] / "fixtures" / "ca" / "dual_audio_ffprobe.json"
# cp1252 carries the interpunct (0xB7), the curly apostrophe (0x92) and every Catalan accent.
SRT = "1\n00:00:01,000 --> 00:00:03,000\nL’àvia diu que el col·legi és a prop.\n"


def _write(tmp_path: Path) -> Path:
    path = tmp_path / "ca.srt"
    path.write_bytes(SRT.encode("cp1252"))
    return path


def test_the_bytes_are_not_utf8_and_carry_the_single_byte_interpunct(tmp_path):
    data = _write(tmp_path).read_bytes()
    assert b"\xb7" in data and b"\x92" in data
    with pytest.raises(UnicodeDecodeError):
        data.decode("utf-8")


def test_the_japanese_ladder_would_mangle_the_file(tmp_path):
    """Negative control: without the profile's cp1252 the ja ladder never yields the line."""
    from anki_miner.utils.subtitle_encoding import load_with_fallback_encoding

    path = _write(tmp_path)
    with pytest.raises(UnicodeDecodeError) as utf8_error:
        path.read_bytes().decode("utf-8")
    try:
        events = load_with_fallback_encoding(path, utf8_error.value)
    except UnicodeDecodeError:
        return
    assert "col·legi" not in events[0].text


def test_cp1252_decodes_and_mines_through_the_catalan_parser(tmp_path):
    parser = get_profile("ca").create_parser(switch_language(AnkiMinerConfig(), "ca"))
    words = parser.parse_subtitle_file(_write(tmp_path))
    assert {"àvia", "dir", "col·legi"} <= {word.mined_form for word in words}
    assert all("�" not in word.sentence for word in words)


def test_the_profile_codes_pick_the_catalan_track(tmp_path):
    proc = MagicMock(returncode=0, stdout=FIXTURE.read_text(encoding="utf-8"), stderr="")
    with patch("anki_miner.utils.audio_track_detector.subprocess.run", return_value=proc):
        video = tmp_path / "trilingual.mkv"
        assert find_japanese_audio_stream(video, codes=get_profile("ca").audio_track_codes).language_tag == "cat"
        assert find_japanese_audio_stream(video, codes=frozenset({"spa", "es", "spanish"})).language_tag == "spa"
        assert find_japanese_audio_stream(video, codes=JAPANESE_LANGUAGE_CODES).language_tag == "jpn"


@pytest.mark.parametrize(
    ("data", "native"),
    [
        ({"automatic_captions": {"ca": [{}], "ca-orig": [{}]}}, True),
        ({"automatic_captions": {"ca": [{}], "es-orig": [{}]}}, False),  # machine-translated from Spanish
        ({"automatic_captions": {"ca": [{}]}, "language": "ca"}, True),  # no -orig key: the language decides
        ({"automatic_captions": {"ca": [{}]}, "language": "es"}, False),
        ({"automatic_captions": {"ca-ES": [{}], "ca-orig": [{}]}}, True),  # ca-ES is a listed code
        ({"automatic_captions": {"ja": [{}], "ja-orig": [{}]}}, False),
    ],
)
def test_caption_codes_detect_native_catalan(data, native):
    assert YouTubeFetcherService._has_native_auto_ja(data, captions=get_profile("ca").captions) is native


@pytest.mark.parametrize(("language", "present"), [("ca", True), ("ca-ES", True), ("cat", False), ("es", False)])
def test_the_audio_track_pattern_is_anchored(language, present):
    data = {"formats": [{"vcodec": "none", "language": language}]}
    assert YouTubeFetcherService._has_ja_audio_track(data, captions=get_profile("ca").captions) is present
