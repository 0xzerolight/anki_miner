"""Croatian: cp1250 subtitles, the legacy scr audio tag and the YouTube caption codes."""

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

FIXTURE = Path(__file__).parents[2] / "fixtures" / "hr" / "dual_audio_ffprobe.json"
#: Every Croatian letter Latin-1 lacks: \u010d=0xE8, \u0107=0xE6, \u0161=0x9A, \u017e=0x9E, \u0111=0xF0 in cp1250.
SRT = (
    "1\n00:00:01,000 --> 00:00:03,000\nKnjige su na stolu.\n\n"
    "2\n00:00:04,000 --> 00:00:06,000\nStudent je ju\u010der pro\u010ditao zanimljivu knjigu.\n\n"
    "3\n00:00:07,000 --> 00:00:09,000\nGra\u0111anin je kupio \u0111a\u010dki priru\u010dnik.\n\n"
    "4\n00:00:10,000 --> 00:00:12,000\n\u017dena \u0161eta psa.\n"
)


@pytest.fixture(scope="module")
def parser():
    return get_profile("hr").create_parser(switch_language(AnkiMinerConfig(), "hr"))


def _write(tmp_path: Path) -> Path:
    path = tmp_path / "hr.srt"
    path.write_bytes(SRT.encode("cp1250"))
    return path


def test_the_croatian_ladder_decodes_cp1250(tmp_path):
    path = _write(tmp_path)
    with pytest.raises(UnicodeDecodeError) as utf8_error:
        path.read_bytes().decode("utf-8")
    profile = get_profile("hr")
    events = load_with_fallback_encoding(
        path,
        utf8_error.value,
        encodings=profile.import_encodings,
        script_check=profile.script.contains_target_script,
    )
    assert "ju\u010der" in events[1].text and "\u0111a\u010dki" in events[2].text


def test_the_japanese_ladder_mangles_the_same_bytes(tmp_path):
    """The negative control: cp932 and the ja encodings read these bytes as something else."""
    path = _write(tmp_path)
    with pytest.raises(UnicodeDecodeError) as utf8_error:
        path.read_bytes().decode("utf-8")
    japanese = get_profile("ja")
    events = load_with_fallback_encoding(
        path,
        utf8_error.value,
        encodings=japanese.import_encodings,
        script_check=japanese.script.contains_target_script,
    )
    assert "ju\u010der" not in " ".join(event.text for event in events)


def test_cp1250_decodes_and_mines_through_the_croatian_parser(parser, tmp_path):
    words = parser.parse_subtitle_file(_write(tmp_path))
    mined = {word.mined_form for word in words}
    assert {"knjiga", "stol", "pro\u010ditati", "gra\u0111anin", "\u017eena"} <= mined
    assert not any("\ufffd" in word.sentence for word in words)


def test_the_profile_codes_pick_the_legacy_croatian_track(tmp_path):
    """Muxers still write the legacy scr tag for Croatian; the ja codes must not claim it."""
    proc = MagicMock(returncode=0, stdout=FIXTURE.read_text(encoding="utf-8"), stderr="")
    with patch("anki_miner.utils.audio_track_detector.subprocess.run", return_value=proc):
        codes = get_profile("hr").audio_track_codes
        assert find_japanese_audio_stream(tmp_path / "bilingual.mkv", codes=codes).language_tag == "scr"
        assert find_japanese_audio_stream(tmp_path / "bilingual.mkv", codes=JAPANESE_LANGUAGE_CODES) is None


@pytest.mark.parametrize(
    ("data", "native"),
    [
        ({"automatic_captions": {"hr": [{}], "hr-orig": [{}]}}, True),
        ({"automatic_captions": {"hr": [{}], "en-orig": [{}]}}, False),
        ({"automatic_captions": {"hr": [{}]}, "language": "hr"}, True),
        ({"automatic_captions": {"hr": [{}]}, "language": "en"}, False),
        ({"automatic_captions": {"sh": [{}], "sh-orig": [{}]}}, False),
    ],
)
def test_caption_codes_detect_native_croatian(data, native):
    """Only the dictionary speaks sh: YouTube captions key on hr."""
    assert YouTubeFetcherService._has_native_auto_ja(data, captions=get_profile("hr").captions) is native


@pytest.mark.parametrize(("language", "present"), [("hr", True), ("hr-HR", True), ("hrv", False), ("sh", False)])
def test_the_audio_track_pattern_is_anchored(language, present):
    data = {"formats": [{"vcodec": "none", "language": language}]}
    assert YouTubeFetcherService._has_ja_audio_track(data, captions=get_profile("hr").captions) is present
