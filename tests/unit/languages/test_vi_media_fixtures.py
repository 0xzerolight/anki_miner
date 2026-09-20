"""Vietnamese: a cp1258 subtitle file, the SDH default, track choice and YouTube caption codes."""

from __future__ import annotations

import unicodedata
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.models.reading import ReadingUnit
from anki_miner.services.youtube_fetcher import YouTubeFetcherService
from anki_miner.utils.audio_track_detector import JAPANESE_LANGUAGE_CODES, find_japanese_audio_stream
from anki_miner.utils.subtitle_encoding import load_with_fallback_encoding

FFPROBE = Path(__file__).parents[2] / "fixtures" / "vi" / "dual_audio_ffprobe.json"
TONES = "\u0300\u0301\u0303\u0309\u0323"
SRT = (
    "1\n00:00:01,000 --> 00:00:03,000\nHôm nay trời đẹp quá.\n\n"
    "2\n00:00:04,000 --> 00:00:06,000\nBác sĩ nói bệnh nhân cần hòa bình.\n"
)
EXPECTED_FRONTS = {"hôm nay", "trời", "đẹp", "bác sĩ", "nói", "bệnh nhân", "cần", "hòa bình"}


def cp1258_bytes(text: str) -> bytes:
    """What a Windows-1258 editor writes: precomposed base letters, the tone as a combining byte."""
    out = bytearray()
    for char in text:
        try:
            out += char.encode("cp1258")
        except UnicodeEncodeError:
            decomposed = unicodedata.normalize("NFD", char)
            base = unicodedata.normalize("NFC", "".join(c for c in decomposed if c not in TONES))
            out += (base + "".join(c for c in decomposed if c in TONES)).encode("cp1258")
    return bytes(out)


@pytest.fixture(scope="module")
def parser():
    return get_profile("vi").create_parser(switch_language(AnkiMinerConfig(), "vi"))


def test_the_built_file_really_is_cp1258_with_combining_tones():
    data = cp1258_bytes(SRT)
    with pytest.raises(UnicodeDecodeError):
        data.decode("utf-8")
    decoded = data.decode("cp1258")
    assert decoded != SRT and unicodedata.normalize("NFC", decoded) == SRT


def test_the_profile_ladder_decodes_it(tmp_path):
    data = cp1258_bytes(SRT)
    with pytest.raises(UnicodeDecodeError) as utf8_error:
        data.decode("utf-8")
    path = tmp_path / "vi.srt"
    path.write_bytes(data)
    profile = get_profile("vi")
    events = load_with_fallback_encoding(
        path,
        utf8_error.value,
        encodings=profile.import_encodings,
        script_check=profile.script.contains_target_script,
    )
    assert any("hòa bình" in unicodedata.normalize("NFC", event.text) for event in events)


def test_cp1258_mines_composed_fronts_and_sentences(parser, tmp_path):
    path = tmp_path / "vi.srt"
    path.write_bytes(cp1258_bytes(SRT))
    words = parser.parse_subtitle_file(path)
    assert {word.mined_form for word in words} >= EXPECTED_FRONTS
    assert all(unicodedata.is_normalized("NFC", word.mined_form) for word in words)
    assert all(unicodedata.is_normalized("NFC", word.sentence) for word in words)


def test_the_sdh_default_strips_vietnamese_speaker_labels_and_sound_effects(parser):
    units = [ReadingUnit(text="ĐỨC: Chào anh. [tiếng súng] Nằm xuống! ♪", index=0, location_label="t")]
    words, _index, _counts = parser.parse_text_units(units, False, subtitle_cleanup=True)
    mined = {word.mined_form for word in words}
    assert {"chào", "nằm", "xuống"} <= mined
    assert not {"đức", "tiếng súng", "súng", "anh"} & mined
    assert all(not word.sentence.startswith("ĐỨC") for word in words)


def test_the_profile_codes_pick_the_vietnamese_track(tmp_path):
    proc = MagicMock(returncode=0, stdout=FFPROBE.read_text(encoding="utf-8"), stderr="")
    with patch("anki_miner.utils.audio_track_detector.subprocess.run", return_value=proc):
        codes = get_profile("vi").audio_track_codes
        assert find_japanese_audio_stream(tmp_path / "dub.mkv", codes=codes).language_tag == "vie"
        assert find_japanese_audio_stream(tmp_path / "dub.mkv", codes=JAPANESE_LANGUAGE_CODES).language_tag == "jpn"


@pytest.mark.parametrize(
    ("data", "native"),
    [
        ({"automatic_captions": {"vi": [{}], "vi-orig": [{}]}}, True),
        ({"automatic_captions": {"vi": [{}], "en-orig": [{}]}}, False),
        ({"automatic_captions": {"vi": [{}]}, "language": "vi"}, True),
        ({"automatic_captions": {"vi": [{}]}, "language": "en"}, False),
    ],
)
def test_caption_codes_detect_native_vietnamese(data, native):
    assert YouTubeFetcherService._has_native_auto_ja(data, captions=get_profile("vi").captions) is native


@pytest.mark.parametrize(("language", "present"), [("vi", True), ("vi-VN", True), ("vie", False), ("ja", False)])
def test_the_audio_track_pattern_is_anchored(language, present):
    data = {"formats": [{"vcodec": "none", "language": language}]}
    assert YouTubeFetcherService._has_ja_audio_track(data, captions=get_profile("vi").captions) is present
