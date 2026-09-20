"""Thai: cp874 subtitles, a bilingual file's track choice and YouTube caption codes (C.3)."""

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

FIXTURES = Path(__file__).parents[2] / "fixtures" / "th"
FFPROBE = FIXTURES / "dual_audio_ffprobe.json"
#: Committed cp874 bytes, written by a throwaway script rather than an editor, so
#: the encoding is part of the fixture and not of whatever wrote the test file.
CP874_SRT = FIXTURES / "subtitle_cp874.srt"


def _write(tmp_path: Path) -> Path:
    path = tmp_path / "th.srt"
    path.write_bytes(CP874_SRT.read_bytes())
    return path


def test_the_profile_declares_the_cp874_ladder():
    assert get_profile("th").import_encodings == ("utf-8-sig", "cp874")


def test_the_fixture_really_is_cp874_and_not_utf8():
    raw = CP874_SRT.read_bytes()
    assert "วันนี้อากาศดีมากครับ" in raw.decode("cp874")
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")


def test_the_japanese_ladder_would_mangle_the_file(tmp_path):
    """Negative control: the ja ladder tries cp932 first and never yields Thai."""
    path = _write(tmp_path)
    with pytest.raises(UnicodeDecodeError) as utf8_error:
        path.read_bytes().decode("utf-8")
    events = load_with_fallback_encoding(path, utf8_error.value)
    assert "วันนี้" not in "".join(event.text for event in events)


@pytest.fixture(scope="module")
def parser():
    """One newmm Trie per module; the parser keeps its tagger past ``conftest``'s per-test clear."""
    return get_profile("th").create_parser(switch_language(AnkiMinerConfig(), "th"))


def test_cp874_decodes_and_mines_through_the_thai_parser(tmp_path, parser):
    words = parser.parse_subtitle_file(_write(tmp_path))
    assert {"อากาศ", "กินข้าว"} <= {word.mined_form for word in words}
    assert all("\N{REPLACEMENT CHARACTER}" not in word.sentence for word in words)


def test_the_profile_codes_pick_the_thai_track(tmp_path):
    proc = MagicMock(returncode=0, stdout=FFPROBE.read_text(encoding="utf-8"), stderr="")
    with patch("anki_miner.utils.audio_track_detector.subprocess.run", return_value=proc):
        codes = get_profile("th").audio_track_codes
        assert find_japanese_audio_stream(tmp_path / "bilingual.mkv", codes=codes).language_tag == "tha"
        assert find_japanese_audio_stream(tmp_path / "bilingual.mkv", codes=JAPANESE_LANGUAGE_CODES) is None


@pytest.mark.parametrize(
    ("automatic", "language", "native"),
    [
        ({"th": [{}], "th-orig": [{}]}, "", True),
        ({"th": [{}]}, "", True),
        ({"th": [{}]}, "de", False),
        ({"th": [{}], "en-orig": [{}]}, "", False),
        ({"de": [{}], "de-orig": [{}]}, "", False),
    ],
)
def test_caption_codes_detect_native_thai(automatic, language, native):
    captions = get_profile("th").captions
    data = {"automatic_captions": automatic, "language": language}
    assert YouTubeFetcherService._has_native_auto_ja(data, captions=captions) is native


@pytest.mark.parametrize(("language", "present"), [("th", True), ("th-TH", True), ("tha", False), ("de", False)])
def test_the_audio_track_pattern_is_anchored(language, present):
    data = {"formats": [{"vcodec": "none", "language": language}]}
    assert YouTubeFetcherService._has_ja_audio_track(data, captions=get_profile("th").captions) is present
