"""Romanian: legacy single-byte subtitles, a trilingual file's track choice, and YouTube caption codes."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.services.youtube_fetcher import YouTubeFetcherService
from anki_miner.utils.audio_track_detector import JAPANESE_LANGUAGE_CODES, find_japanese_audio_stream

FIXTURE = Path(__file__).parents[2] / "fixtures" / "ro" / "dual_audio_ffprobe.json"
# The legacy cedilla spelling cp1250 carries (0xBA s-cedilla, 0xFE t-cedilla, 0xAA S-cedilla) beside a-breve,
# a-circumflex and i-circumflex.
LINE = "\u015etefan \u015fi fata îi spun că \u015ftiin\u0163a e grea."
COMMA_BELOW_LINE = "Ștefan și fata îi spun că știința e grea."


def _srt(tmp_path: Path, text: str, codec: str) -> Path:
    path = tmp_path / f"ro-{codec}.srt"
    path.write_bytes(f"1\n00:00:01,000 --> 00:00:03,000\n{text}\n".encode(codec))
    return path


def test_the_bytes_are_not_utf8_and_carry_the_single_byte_letters(tmp_path):
    data = _srt(tmp_path, LINE, "cp1250").read_bytes()
    assert b"\xba" in data and b"\xfe" in data and b"\xaa" in data
    with pytest.raises(UnicodeDecodeError):
        data.decode("utf-8")


def test_the_profile_ladder_decodes_the_file_without_the_detector(tmp_path):
    """The cp1250 rung is what makes the decode deterministic.

    Observed 2026-09-17: the built-in Japanese ladder reaches the same text for this file, but only through
    its optional charset-normalizer leg (the ladder itself has no single-byte Romanian rung), so the profile's
    ``import_encodings`` is what the app relies on.
    """
    from anki_miner.utils.subtitle_encoding import load_with_fallback_encoding

    path = _srt(tmp_path, LINE, "cp1250")
    with pytest.raises(UnicodeDecodeError) as utf8_error:
        path.read_bytes().decode("utf-8")
    profile = get_profile("ro")
    assert profile.import_encodings == ("utf-8-sig", "cp1250")
    events = load_with_fallback_encoding(path, utf8_error.value, encodings=profile.import_encodings)
    assert events[0].text == LINE  # verbatim: the parser, not the loader, folds the cedillas


@pytest.fixture(scope="module")
def parser():
    return get_profile("ro").create_parser(switch_language(AnkiMinerConfig(), "ro"))


@pytest.mark.parametrize(
    ("text", "codec"),
    [
        (LINE, "cp1250"),  # a legacy cedilla file
        # R10: ISO-8859-16's comma-below bytes decode as the cedilla letters under cp1250, and normalize folds them
        (COMMA_BELOW_LINE, "iso8859_16"),
    ],
)
def test_a_single_byte_file_decodes_and_mines_in_comma_below(parser, tmp_path, text, codec):
    words = parser.parse_subtitle_file(_srt(tmp_path, text, codec))
    assert {"fată", "spune", "știință"} <= {word.mined_form for word in words}
    assert all(word.sentence == COMMA_BELOW_LINE for word in words)


def test_the_profile_codes_pick_the_romanian_track(tmp_path):
    proc = MagicMock(returncode=0, stdout=FIXTURE.read_text(encoding="utf-8"), stderr="")
    with patch("anki_miner.utils.audio_track_detector.subprocess.run", return_value=proc):
        video = tmp_path / "trilingual.mkv"
        assert find_japanese_audio_stream(video, codes=get_profile("ro").audio_track_codes).language_tag == "rum"
        assert find_japanese_audio_stream(video, codes=frozenset({"eng", "en", "english"})).language_tag == "eng"
        assert find_japanese_audio_stream(video, codes=JAPANESE_LANGUAGE_CODES).language_tag == "jpn"


@pytest.mark.parametrize(
    ("data", "native"),
    [
        ({"automatic_captions": {"ro": [{}], "ro-orig": [{}]}}, True),
        ({"automatic_captions": {"ro": [{}], "en-orig": [{}]}}, False),  # machine-translated from English
        ({"automatic_captions": {"ro": [{}]}, "language": "ro"}, True),  # no -orig key: the language decides
        ({"automatic_captions": {"ro": [{}]}, "language": "it"}, False),
        ({"automatic_captions": {"ja": [{}], "ja-orig": [{}]}}, False),
    ],
)
def test_caption_codes_detect_native_romanian(data, native):
    assert YouTubeFetcherService._has_native_auto_ja(data, captions=get_profile("ro").captions) is native


@pytest.mark.parametrize(("language", "present"), [("ro", True), ("ro-RO", True), ("rom", False), ("ru", False)])
def test_the_audio_track_pattern_is_anchored(language, present):
    data = {"formats": [{"vcodec": "none", "language": language}]}
    assert YouTubeFetcherService._has_ja_audio_track(data, captions=get_profile("ro").captions) is present
