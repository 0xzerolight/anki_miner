"""Italian: cp1252 subtitles, a bilingual file's track choice, and YouTube caption codes."""

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

FIXTURE = Path(__file__).parents[2] / "fixtures" / "it" / "dual_audio_ffprobe.json"
SRT = (
    "1\n00:00:01,000 --> 00:00:03,000\nPerché è già così tardi?\n\n"
    "2\n00:00:04,000 --> 00:00:06,000\nL’uomo è là… più città.\n"
)
#: What the ja ladder makes of these bytes (probed on main d8c2da83): è->č, à->ŕ, ì->ě, ù->ů.
MOJIBAKE = ("č", "ŕ", "ě", "ů", "�")


def _write(tmp_path: Path) -> Path:
    path = tmp_path / "it.srt"
    path.write_bytes(SRT.encode("cp1252"))
    return path


def test_the_japanese_ladder_mangles_the_file(tmp_path):
    """Negative control, reproduced on main d8c2da83: the ja ladder decodes without raising, as mojibake."""
    path = _write(tmp_path)
    with pytest.raises(UnicodeDecodeError) as utf8_error:
        path.read_bytes().decode("utf-8")
    events = load_with_fallback_encoding(path, utf8_error.value)
    assert "giŕ" in events[0].text and "già" not in events[0].text


def test_cp1252_decodes_and_mines_through_the_italian_parser(tmp_path):
    parser = get_profile("it").create_parser(switch_language(AnkiMinerConfig(), "it"))
    words = parser.parse_subtitle_file(_write(tmp_path))
    assert {"perché", "già", "così", "tardi", "uomo", "città"} <= {word.mined_form for word in words}
    assert not any(mark in word.sentence for word in words for mark in MOJIBAKE)


def test_the_profile_codes_pick_the_italian_track(tmp_path):
    proc = MagicMock(returncode=0, stdout=FIXTURE.read_text(encoding="utf-8"), stderr="")
    with patch("anki_miner.utils.audio_track_detector.subprocess.run", return_value=proc):
        codes = get_profile("it").audio_track_codes
        assert find_japanese_audio_stream(tmp_path / "bilingual.mkv", codes=codes).language_tag == "ita"
        assert (
            find_japanese_audio_stream(tmp_path / "bilingual.mkv", codes=JAPANESE_LANGUAGE_CODES).language_tag == "jpn"
        )


@pytest.mark.parametrize(
    ("data", "native"),
    [
        ({"automatic_captions": {"it": [{}], "it-orig": [{}]}}, True),
        ({"automatic_captions": {"it": [{}], "en-orig": [{}]}}, False),  # machine-translated from English
        ({"automatic_captions": {"it": [{}]}, "language": "it"}, True),
        ({"automatic_captions": {"it": [{}]}, "language": "en"}, False),
        ({"automatic_captions": {"ja": [{}], "ja-orig": [{}]}}, False),
    ],
)
def test_caption_codes_detect_native_italian(data, native):
    assert YouTubeFetcherService._has_native_auto_ja(data, captions=get_profile("it").captions) is native


@pytest.mark.parametrize(("language", "present"), [("it", True), ("it-IT", True), ("ita", False), ("ja", False)])
def test_the_audio_track_pattern_is_anchored(language, present):
    data = {"formats": [{"vcodec": "none", "language": language}]}
    assert YouTubeFetcherService._has_ja_audio_track(data, captions=get_profile("it").captions) is present
