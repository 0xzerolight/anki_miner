"""Indonesian: a legacy cp1252 subtitle, the track choice (``ind`` and the legacy ``in``), YouTube caption codes."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.services.youtube_fetcher import YouTubeFetcherService
from anki_miner.utils.audio_track_detector import JAPANESE_LANGUAGE_CODES, find_japanese_audio_stream

FIXTURE = Path(__file__).parents[2] / "fixtures" / "id" / "dual_audio_ffprobe.json"
LINE = "Jum’at dia membeli roti di café “Enak” — lezat!"


def _srt(tmp_path: Path, text: str, codec: str) -> Path:
    path = tmp_path / f"id-{codec}.srt"
    path.write_bytes(f"1\n00:00:01,000 --> 00:00:03,000\n{text}\n".encode(codec))
    return path


def test_the_bytes_are_cp1252_punctuation_and_not_utf8(tmp_path):
    data = _srt(tmp_path, LINE, "cp1252").read_bytes()
    assert b"\x92" in data and b"\x93" in data and b"\x94" in data and b"\x97" in data and b"\xe9" in data
    with pytest.raises(UnicodeDecodeError):
        data.decode("utf-8")


def test_the_profile_ladder_decodes_the_file(tmp_path):
    from anki_miner.utils.subtitle_encoding import load_with_fallback_encoding

    path = _srt(tmp_path, LINE, "cp1252")
    with pytest.raises(UnicodeDecodeError) as utf8_error:
        path.read_bytes().decode("utf-8")
    profile = get_profile("id")
    assert profile.import_encodings == ("utf-8-sig", "cp1252")
    events = load_with_fallback_encoding(path, utf8_error.value, encodings=profile.import_encodings)
    assert events[0].text == LINE


@pytest.fixture(scope="module")
def parser():
    return get_profile("id").create_parser(switch_language(AnkiMinerConfig(), "id"))


def test_a_cp1252_file_decodes_and_mines(parser, tmp_path):
    """The curly apostrophe keeps ``Jum'at`` one word; ``cafe'`` folds to ``cafe``; ``Enak`` mid-line is a name."""
    words = parser.parse_subtitle_file(_srt(tmp_path, LINE, "cp1252"))
    fronts = {word.mined_form for word in words}
    assert {"jum’at", "membeli", "roti", "cafe", "lezat"} <= fronts
    assert "enak" not in fronts and "dia" not in fronts and "di" not in fronts
    assert all(word.sentence in LINE for word in words)


@pytest.mark.parametrize("tag", ["ind", "in"])
def test_the_profile_codes_pick_the_indonesian_track(tmp_path, tag):
    """``in`` is the pre-1989 code Java-muxed files and Android locales still write."""
    text = FIXTURE.read_text(encoding="utf-8").replace('"language": "ind"', f'"language": "{tag}"')
    proc = MagicMock(returncode=0, stdout=text, stderr="")
    with patch("anki_miner.utils.audio_track_detector.subprocess.run", return_value=proc):
        video = tmp_path / "trilingual.mkv"
        assert find_japanese_audio_stream(video, codes=get_profile("id").audio_track_codes).language_tag == tag
        assert find_japanese_audio_stream(video, codes=frozenset({"eng", "en", "english"})).language_tag == "eng"
        assert find_japanese_audio_stream(video, codes=JAPANESE_LANGUAGE_CODES).language_tag == "jpn"


def test_a_malay_track_is_not_indonesian(tmp_path):
    text = FIXTURE.read_text(encoding="utf-8").replace('"language": "ind"', '"language": "msa"')
    proc = MagicMock(returncode=0, stdout=text, stderr="")
    with patch("anki_miner.utils.audio_track_detector.subprocess.run", return_value=proc):
        stream = find_japanese_audio_stream(tmp_path / "x.mkv", codes=get_profile("id").audio_track_codes)
    assert stream is None or stream.language_tag != "msa"


@pytest.mark.parametrize(
    ("data", "native"),
    [
        ({"automatic_captions": {"id": [{}], "id-orig": [{}]}}, True),
        ({"automatic_captions": {"id": [{}], "en-orig": [{}]}}, False),  # machine-translated from English
        ({"automatic_captions": {"id": [{}]}, "language": "id"}, True),  # no -orig key: the language decides
        ({"automatic_captions": {"id": [{}]}, "language": "ms"}, False),
        ({"automatic_captions": {"ja": [{}], "ja-orig": [{}]}}, False),
    ],
)
def test_caption_codes_detect_native_indonesian(data, native):
    assert YouTubeFetcherService._has_native_auto_ja(data, captions=get_profile("id").captions) is native


@pytest.mark.parametrize(("language", "present"), [("id", True), ("id-ID", True), ("ind", False), ("it", False)])
def test_the_audio_track_pattern_is_anchored(language, present):
    data = {"formats": [{"vcodec": "none", "language": language}]}
    assert YouTubeFetcherService._has_ja_audio_track(data, captions=get_profile("id").captions) is present
