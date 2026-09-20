"""Spanish: cp1252 subtitles, a bilingual file's track choice, and YouTube caption codes."""

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

FIXTURE = Path(__file__).parents[2] / "fixtures" / "es" / "dual_audio_ffprobe.json"
SRT = "1\n00:00:01,000 --> 00:00:03,000\n¿El niño come una manzana en el año nuevo?\n"


def _write(tmp_path: Path) -> Path:
    path = tmp_path / "es.srt"
    path.write_bytes(SRT.encode("cp1252"))
    return path


def test_the_japanese_ladder_would_mangle_the_file(tmp_path):
    """Negative control: cp932 decodes ¿ and ñ without raising (niño -> ni + a private-use character)."""
    path = _write(tmp_path)
    with pytest.raises(UnicodeDecodeError) as utf8_error:
        path.read_bytes().decode("utf-8")
    try:
        events = load_with_fallback_encoding(path, utf8_error.value)
    except UnicodeDecodeError:
        return
    assert "niño" not in events[0].text


def test_cp1252_decodes_and_mines_through_the_spanish_parser(tmp_path):
    parser = get_profile("es").create_parser(switch_language(AnkiMinerConfig(), "es"))
    words = parser.parse_subtitle_file(_write(tmp_path))
    assert {"niño", "comer", "manzana", "año"} <= {word.mined_form for word in words}
    # U+FFFD, and the two characters cp932 made of ¿ and ñ in the A1 probe (half-width ｿ, private-use U+E0EB).
    assert all(bad not in word.sentence for bad in ("\ufffd", "\uff7f", "\ue0eb") for word in words)


def test_the_profile_codes_pick_the_spanish_track(tmp_path):
    proc = MagicMock(returncode=0, stdout=FIXTURE.read_text(encoding="utf-8"), stderr="")
    with patch("anki_miner.utils.audio_track_detector.subprocess.run", return_value=proc):
        codes = get_profile("es").audio_track_codes
        assert find_japanese_audio_stream(tmp_path / "bilingual.mkv", codes=codes).language_tag == "spa"
        assert (
            find_japanese_audio_stream(tmp_path / "bilingual.mkv", codes=JAPANESE_LANGUAGE_CODES).language_tag == "jpn"
        )


@pytest.mark.parametrize(
    ("data", "native"),
    [
        # The fetcher's rule (youtube_fetcher.py `_has_native_auto_ja`, ES-5): one of the profile's codes must exist;
        # an es-orig key means native; another *-orig means translated; no *-orig falls back to `language` (empty =
        # native).
        ({"automatic_captions": {"es": [{}], "es-orig": [{}]}}, True),
        ({"automatic_captions": {"es": [{}], "es-419": [{}], "es-orig": [{}]}}, True),
        ({"automatic_captions": {"es-419": [{}], "es-orig": [{}]}}, True),  # es-419 is a listed code
        ({"automatic_captions": {"es": [{}], "en-orig": [{}]}}, False),  # machine-translated from English
        ({"automatic_captions": {"es": [{}]}}, True),  # no *-orig and no language: native
        ({"automatic_captions": {"es": [{}]}, "language": "en"}, False),
        ({"automatic_captions": {"es": [{}]}, "language": "es-419"}, True),  # a listed regional code
        ({"automatic_captions": {"ja": [{}], "ja-orig": [{}]}}, False),
    ],
)
def test_caption_codes_detect_native_spanish(data, native):
    captions = get_profile("es").captions
    assert YouTubeFetcherService._has_native_auto_ja(data, captions=captions) is native


@pytest.mark.parametrize(
    ("language", "present"),
    [("es", True), ("es-419", True), ("es-ES", True), ("spa", False), ("est", False), ("en", False)],
)
def test_the_audio_track_pattern_is_anchored(language, present):
    data = {"formats": [{"vcodec": "none", "language": language}]}
    assert YouTubeFetcherService._has_ja_audio_track(data, captions=get_profile("es").captions) is present
