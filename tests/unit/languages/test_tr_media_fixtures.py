"""Turkish: cp1254 subtitles, a bilingual file's track choice and YouTube caption codes (B.2)."""

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

FIXTURE = Path(__file__).parents[2] / "fixtures" / "tr" / "dual_audio_ffprobe.json"
SRT = "1\n00:00:01,000 --> 00:00:03,000\nIşığı söndür, çocuğu uyut. Şişeyi İstanbul'a götür.\n"


def _write(tmp_path: Path) -> Path:
    path = tmp_path / "tr.srt"
    path.write_bytes(SRT.encode("cp1254"))
    return path


def test_the_profile_declares_the_cp1254_ladder():
    assert get_profile("tr").import_encodings == ("utf-8-sig", "cp1254")


def test_the_japanese_ladder_would_mangle_the_file(tmp_path):
    """Negative control: ı ğ ş İ Ş sit where cp1252 has ý ð þ Ý Þ."""
    path = _write(tmp_path)
    with pytest.raises(UnicodeDecodeError) as utf8_error:
        path.read_bytes().decode("utf-8")
    events = load_with_fallback_encoding(path, utf8_error.value)
    assert "Işığı" not in events[0].text and "Şişeyi" not in events[0].text


@pytest.fixture(scope="module")
def parser():
    """One zeyrek build per module (4.3-5.6 s); the parser keeps its tagger past ``conftest``'s per-test clear."""
    return get_profile("tr").create_parser(switch_language(AnkiMinerConfig(), "tr"))


def test_cp1254_decodes_and_mines_through_the_turkish_parser(tmp_path, parser):
    words = parser.parse_subtitle_file(_write(tmp_path))
    assert {"ışık", "söndürmek", "çocuk", "şişe", "götürmek"} <= {word.mined_form for word in words}
    assert all("�" not in word.sentence and "Işığı" in word.sentence for word in words)


def test_the_profile_codes_pick_the_turkish_track(tmp_path):
    proc = MagicMock(returncode=0, stdout=FIXTURE.read_text(encoding="utf-8"), stderr="")
    with patch("anki_miner.utils.audio_track_detector.subprocess.run", return_value=proc):
        codes = get_profile("tr").audio_track_codes
        assert find_japanese_audio_stream(tmp_path / "bilingual.mkv", codes=codes).language_tag == "tur"
        japanese = find_japanese_audio_stream(tmp_path / "bilingual.mkv", codes=JAPANESE_LANGUAGE_CODES)
        assert japanese.language_tag == "jpn"


@pytest.mark.parametrize(
    ("automatic", "language", "native"),
    [
        ({"tr": [{}], "tr-orig": [{}]}, "", True),
        ({"tr": [{}]}, "", True),
        ({"tr": [{}]}, "de", False),
        ({"tr": [{}], "en-orig": [{}]}, "", False),
        ({"de": [{}], "de-orig": [{}]}, "", False),
    ],
)
def test_caption_codes_detect_native_turkish(automatic, language, native):
    captions = get_profile("tr").captions
    data = {"automatic_captions": automatic, "language": language}
    assert YouTubeFetcherService._has_native_auto_ja(data, captions=captions) is native


@pytest.mark.parametrize(("language", "present"), [("tr", True), ("tr-TR", True), ("tur", False), ("de", False)])
def test_the_audio_track_pattern_is_anchored(language, present):
    data = {"formats": [{"vcodec": "none", "language": language}]}
    assert YouTubeFetcherService._has_ja_audio_track(data, captions=get_profile("tr").captions) is present
