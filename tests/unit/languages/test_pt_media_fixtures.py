"""Portuguese: cp1252 subtitles, a bilingual file's track choice, and YouTube caption codes."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.services.youtube_fetcher import YouTubeFetcherService
from anki_miner.utils.audio_track_detector import (
    JAPANESE_LANGUAGE_CODES,
    find_japanese_audio_stream,
    matches_language_tag,
)
from anki_miner.utils.subtitle_encoding import load_with_fallback_encoding

FIXTURE = Path(__file__).parents[2] / "fixtures" / "pt" / "dual_audio_ffprobe.json"
SRT = "1\n00:00:01,000 --> 00:00:03,000\nNão sei, coração. A canção acabou.\n"


def _write(tmp_path: Path) -> Path:
    path = tmp_path / "pt.srt"
    path.write_bytes(SRT.encode("cp1252"))
    return path


def test_the_japanese_ladder_would_mangle_the_file(tmp_path):
    """Negative control: cp932 decodes these cp1252 bytes without raising (Não -> N縊)."""
    path = _write(tmp_path)
    with pytest.raises(UnicodeDecodeError) as utf8_error:
        path.read_bytes().decode("utf-8")
    assert "縊" in path.read_bytes().decode("cp932")
    events = load_with_fallback_encoding(path, utf8_error.value)
    assert "coração" not in events[0].text


def test_cp1252_decodes_and_mines_through_the_portuguese_parser(tmp_path):
    parser = get_profile("pt").create_parser(switch_language(AnkiMinerConfig(), "pt"))
    words = parser.parse_subtitle_file(_write(tmp_path))
    assert {"saber", "coração", "canção", "acabar"} <= {word.mined_form for word in words}
    assert all("�" not in word.sentence and "縊" not in word.sentence for word in words)


def test_the_profile_codes_pick_the_portuguese_track(tmp_path):
    proc = MagicMock(returncode=0, stdout=FIXTURE.read_text(encoding="utf-8"), stderr="")
    with patch("anki_miner.utils.audio_track_detector.subprocess.run", return_value=proc):
        codes = get_profile("pt").audio_track_codes
        assert find_japanese_audio_stream(tmp_path / "bilingual.mkv", codes=codes).language_tag == "por"
        assert (
            find_japanese_audio_stream(tmp_path / "bilingual.mkv", codes=JAPANESE_LANGUAGE_CODES).language_tag == "jpn"
        )


@pytest.mark.parametrize(
    ("tag", "matches"),
    [("por", True), ("pt", True), ("pt-BR", True), ("pt-PT", True), ("pob", False), ("es", False)],
)
def test_regional_track_tags_match_through_the_primary_subtag(tag, matches):
    assert matches_language_tag(tag, get_profile("pt").audio_track_codes) is matches


@pytest.mark.parametrize(
    ("automatic", "language", "native"),
    [
        ({"pt": [{}], "pt-orig": [{}]}, None, True),
        ({"pt": [{}], "en-orig": [{}]}, None, False),  # a translation into pt, original English
        ({"pt": [{}]}, "pt-BR", True),  # no -orig key: the language field decides, regional variants included
        ({"pt": [{}]}, "es", False),
        ({"pt-BR": [{}], "pt-orig": [{}]}, None, True),  # pt-BR is a listed code
    ],
)
def test_caption_codes_detect_native_portuguese(automatic, language, native):
    data = {"automatic_captions": automatic, "language": language}
    assert YouTubeFetcherService._has_native_auto_ja(data, captions=get_profile("pt").captions) is native


@pytest.mark.parametrize(("language", "present"), [("pt", True), ("pt-BR", True), ("por", False), ("es", False)])
def test_the_audio_track_pattern_is_anchored(language, present):
    data = {"formats": [{"vcodec": "none", "language": language}]}
    assert YouTubeFetcherService._has_ja_audio_track(data, captions=get_profile("pt").captions) is present
