"""French: a cp1252 subtitle, a bilingual file's track choice, and YouTube caption codes."""

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

FIXTURE = Path(__file__).parents[2] / "fixtures" / "fr" / "dual_audio_ffprobe.json"
#: ’ (0x92), œ (0x9C) and « » (0xAB 0xBB) are the cp1252 bytes Latin-1 cannot decode and cp932 swallows.
SRT = (
    "1\n00:00:01,000 --> 00:00:03,000\nL’homme a mangé un œuf à la crème.\n\n"
    "2\n00:00:04,000 --> 00:00:06,000\n« Déjà ? » dit-il.\n"
)


def _write(tmp_path: Path) -> Path:
    path = tmp_path / "fr.srt"
    path.write_bytes(SRT.encode("cp1252"))
    return path


def test_the_japanese_ladder_would_mangle_the_file(tmp_path):
    """Negative control (memory: cp932 swallows cp1252 apostrophes)."""
    path = _write(tmp_path)
    with pytest.raises(UnicodeDecodeError) as utf8_error:
        path.read_bytes().decode("utf-8")
    try:
        events = load_with_fallback_encoding(path, utf8_error.value)
    except UnicodeDecodeError:
        return
    assert "L’homme" not in events[0].text


def test_cp1252_decodes_and_mines_through_the_french_parser(tmp_path):
    parser = get_profile("fr").create_parser(switch_language(AnkiMinerConfig(), "fr"))
    words = parser.parse_subtitle_file(_write(tmp_path))
    assert {"homme", "œuf", "crème", "déjà"} <= {word.mined_form for word in words}
    sentences = {word.sentence for word in words}
    assert "L’homme a mangé un œuf à la crème." in sentences
    assert all("�" not in sentence for sentence in sentences)


def test_the_profile_codes_pick_the_french_track(tmp_path):
    proc = MagicMock(returncode=0, stdout=FIXTURE.read_text(encoding="utf-8"), stderr="")
    with patch("anki_miner.utils.audio_track_detector.subprocess.run", return_value=proc):
        codes = get_profile("fr").audio_track_codes
        assert find_japanese_audio_stream(tmp_path / "bilingual.mkv", codes=codes).language_tag == "fre"
        assert (
            find_japanese_audio_stream(tmp_path / "bilingual.mkv", codes=JAPANESE_LANGUAGE_CODES).language_tag == "jpn"
        )


@pytest.mark.parametrize(
    ("data", "native"),
    [
        ({"automatic_captions": {"fr": [{}], "fr-orig": [{}]}}, True),
        ({"automatic_captions": {"fr": [{}], "en-orig": [{}]}}, False),  # machine-translated from English
        ({"automatic_captions": {"fr": [{}]}, "language": "fr-CA"}, True),
        ({"automatic_captions": {"fr": [{}]}, "language": "en"}, False),
        ({"automatic_captions": {"fr-CA": [{}], "fr-orig": [{}]}}, False),  # the fetcher requires the primary key
    ],
)
def test_caption_codes_detect_native_french(data, native):
    """Pins youtube_fetcher.py:385-449 as it is: no fetcher change in this stage."""
    assert YouTubeFetcherService._has_native_auto_ja(data, captions=get_profile("fr").captions) is native


@pytest.mark.parametrize(
    ("language", "present"), [("fr", True), ("fr-CA", True), ("fre", False), ("frr", False), ("ja", False)]
)
def test_the_audio_track_pattern_is_anchored(language, present):
    data = {"formats": [{"vcodec": "none", "language": language}]}
    assert YouTubeFetcherService._has_ja_audio_track(data, captions=get_profile("fr").captions) is present
