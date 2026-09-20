"""Slovenian: cp1250 subtitles, the legacy slv audio tag and the YouTube caption codes."""

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

FIXTURE = Path(__file__).parents[2] / "fixtures" / "sl" / "dual_audio_ffprobe.json"
#: Every Slovenian letter Latin-1 lacks: č=0xE8, š=0x9A, ž=0x9E in cp1250.
SRT = (
    "1\n00:00:01,000 --> 00:00:03,000\nKnjige so na mizi.\n\n"
    "2\n00:00:04,000 --> 00:00:06,000\nŠtudent je včeraj prebral zanimivo knjigo.\n\n"
    "3\n00:00:07,000 --> 00:00:09,000\nŽivali so lačne.\n\n"
    "4\n00:00:10,000 --> 00:00:12,000\nČebela je na cvetu.\n"
)


@pytest.fixture(scope="module")
def parser():
    return get_profile("sl").create_parser(switch_language(AnkiMinerConfig(), "sl"))


def _write(tmp_path: Path) -> Path:
    path = tmp_path / "sl.srt"
    path.write_bytes(SRT.encode("cp1250"))
    return path


def test_the_slovenian_ladder_decodes_cp1250(tmp_path):
    path = _write(tmp_path)
    with pytest.raises(UnicodeDecodeError) as utf8_error:
        path.read_bytes().decode("utf-8")
    profile = get_profile("sl")
    events = load_with_fallback_encoding(
        path,
        utf8_error.value,
        encodings=profile.import_encodings,
        script_check=profile.script.contains_target_script,
    )
    assert "Študent" in events[1].text and "včeraj" in events[1].text
    assert "Živali" in events[2].text and "Čebela" in events[3].text


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
    assert "včeraj" not in " ".join(event.text for event in events)


def test_cp1250_decodes_and_mines_through_the_slovenian_parser(parser, tmp_path):
    words = parser.parse_subtitle_file(_write(tmp_path))
    mined = {word.mined_form for word in words}
    assert {"knjiga", "miza", "študent", "prebrati", "žival", "čebela"} <= mined
    assert not any("�" in word.sentence for word in words)


def test_the_profile_codes_pick_the_legacy_slovenian_track(tmp_path):
    """Muxers still write the legacy slv tag for Slovenian; the ja codes must not claim it."""
    proc = MagicMock(returncode=0, stdout=FIXTURE.read_text(encoding="utf-8"), stderr="")
    with patch("anki_miner.utils.audio_track_detector.subprocess.run", return_value=proc):
        codes = get_profile("sl").audio_track_codes
        assert find_japanese_audio_stream(tmp_path / "bilingual.mkv", codes=codes).language_tag == "slv"
        assert find_japanese_audio_stream(tmp_path / "bilingual.mkv", codes=JAPANESE_LANGUAGE_CODES) is None


@pytest.mark.parametrize(
    ("data", "native"),
    [
        ({"automatic_captions": {"sl": [{}], "sl-orig": [{}]}}, True),
        ({"automatic_captions": {"sl": [{}], "en-orig": [{}]}}, False),
        ({"automatic_captions": {"sl": [{}]}, "language": "sl"}, True),
        ({"automatic_captions": {"sl": [{}]}, "language": "en"}, False),
        ({"automatic_captions": {"slv": [{}], "slv-orig": [{}]}}, False),
    ],
)
def test_caption_codes_detect_native_slovenian(data, native):
    """YouTube keys on the ISO 639-1 code; slv is the muxer tag, never a caption code."""
    assert YouTubeFetcherService._has_native_auto_ja(data, captions=get_profile("sl").captions) is native


@pytest.mark.parametrize(("language", "present"), [("sl", True), ("sl-SI", True), ("slv", False), ("sk", False)])
def test_the_audio_track_pattern_is_anchored(language, present):
    data = {"formats": [{"vcodec": "none", "language": language}]}
    assert YouTubeFetcherService._has_ja_audio_track(data, captions=get_profile("sl").captions) is present
