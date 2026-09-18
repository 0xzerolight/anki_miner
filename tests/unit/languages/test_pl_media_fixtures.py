"""Polish: legacy single-byte subtitles, a trilingual file's track choice, and YouTube caption codes."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.services.youtube_fetcher import YouTubeFetcherService
from anki_miner.utils.audio_track_detector import JAPANESE_LANGUAGE_CODES, find_japanese_audio_stream

FIXTURE = Path(__file__).parents[2] / "fixtures" / "pl" / "dual_audio_ffprobe.json"
LINE = "Łódź płynie, a mąż je ćwikłę. Świeża żaba śpi."


def _srt(tmp_path: Path, text: str, codec: str) -> Path:
    path = tmp_path / f"pl-{codec}.srt"
    path.write_bytes(f"1\n00:00:01,000 --> 00:00:03,000\n{text}\n".encode(codec))
    return path


def test_the_bytes_are_not_utf8_and_carry_the_single_byte_letters(tmp_path):
    data = _srt(tmp_path, LINE, "cp1250").read_bytes()
    assert b"\xa3" in data and b"\xb9" in data and b"\x9c" in data  # Ł, ą, ś
    with pytest.raises(UnicodeDecodeError):
        data.decode("utf-8")


def test_the_profile_ladder_decodes_the_file(tmp_path):
    from anki_miner.utils.subtitle_encoding import load_with_fallback_encoding

    path = _srt(tmp_path, LINE, "cp1250")
    with pytest.raises(UnicodeDecodeError) as utf8_error:
        path.read_bytes().decode("utf-8")
    profile = get_profile("pl")
    assert profile.import_encodings == ("utf-8-sig", "cp1250")
    events = load_with_fallback_encoding(path, utf8_error.value, encodings=profile.import_encodings)
    assert events[0].text == LINE


def test_an_iso_8859_2_file_is_a_documented_limit(tmp_path):
    """P12: the two codecs differ only on ą ś ź Ą Ś Ź, and cp1250 decodes those bytes without raising,
    so no ladder rung after cp1250 is reachable. The file decodes, with those letters wrong."""
    text = _srt(tmp_path, LINE, "iso8859_2").read_bytes().decode("cp1250")
    assert "m±ż" in text and "¦wieża" in text and "¶pi" in text  # ą ś as ± ¦ ¶
    assert "mąż" not in text


@pytest.fixture(scope="module")
def parser():
    return get_profile("pl").create_parser(switch_language(AnkiMinerConfig(), "pl"))


def test_a_cp1250_file_decodes_and_mines(parser, tmp_path):
    """The decoded letters reach the tagger, so ą ć ł ó ś ż words front as Polish lemmas.

    Observed misses on this line, left alone: ``Świeża`` is a capitalised identity lemma (Task 12
    repairs it) and ``ćwikłę`` keeps its accusative surface (a lemmatiser miss, plan P18).
    """
    words = parser.parse_subtitle_file(_srt(tmp_path, LINE, "cp1250"))
    assert {"łódź", "płynąć", "mąż", "spać"} <= {word.mined_form for word in words}
    assert all(word.sentence in LINE for word in words)


def test_the_profile_codes_pick_the_polish_track(tmp_path):
    proc = MagicMock(returncode=0, stdout=FIXTURE.read_text(encoding="utf-8"), stderr="")
    with patch("anki_miner.utils.audio_track_detector.subprocess.run", return_value=proc):
        video = tmp_path / "trilingual.mkv"
        assert find_japanese_audio_stream(video, codes=get_profile("pl").audio_track_codes).language_tag == "pol"
        assert find_japanese_audio_stream(video, codes=frozenset({"eng", "en", "english"})).language_tag == "eng"
        assert find_japanese_audio_stream(video, codes=JAPANESE_LANGUAGE_CODES).language_tag == "jpn"


@pytest.mark.parametrize(
    ("data", "native"),
    [
        ({"automatic_captions": {"pl": [{}], "pl-orig": [{}]}}, True),
        ({"automatic_captions": {"pl": [{}], "en-orig": [{}]}}, False),  # machine-translated from English
        ({"automatic_captions": {"pl": [{}]}, "language": "pl"}, True),  # no -orig key: the language decides
        ({"automatic_captions": {"pl": [{}]}, "language": "cs"}, False),
        ({"automatic_captions": {"ja": [{}], "ja-orig": [{}]}}, False),
    ],
)
def test_caption_codes_detect_native_polish(data, native):
    assert YouTubeFetcherService._has_native_auto_ja(data, captions=get_profile("pl").captions) is native


@pytest.mark.parametrize(("language", "present"), [("pl", True), ("pl-PL", True), ("pol", False), ("pt", False)])
def test_the_audio_track_pattern_is_anchored(language, present):
    data = {"formats": [{"vcodec": "none", "language": language}]}
    assert YouTubeFetcherService._has_ja_audio_track(data, captions=get_profile("pl").captions) is present
