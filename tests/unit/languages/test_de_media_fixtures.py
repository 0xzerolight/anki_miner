"""German: cp1252 subtitles, NFD umlauts, a bilingual file's track choice, and YouTube caption codes."""

from __future__ import annotations

import unicodedata
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.services.youtube_fetcher import YouTubeFetcherService
from anki_miner.utils.audio_track_detector import JAPANESE_LANGUAGE_CODES, find_japanese_audio_stream
from anki_miner.utils.subtitle_encoding import load_with_fallback_encoding

FIXTURE = Path(__file__).parents[2] / "fixtures" / "de" / "dual_audio_ffprobe.json"
LINE = "„Das Mädchen trägt heute eine schöne Jacke“, sagte sie – wirklich!"
SRT = f"1\n00:00:01,000 --> 00:00:03,000\n{LINE}\n"
#: cp932 decodes this cp1252 line without raising and garbles it (geht’s -> geht痴, Mädchen -> M臈chen); LINE's
#: en dash makes cp932 fail, so the ja ladder would reach the right answer by accident (judge DE-1).
CONTROL = "Wie geht’s dir, Mädchen?"
CONTROL_SRT = f"1\n00:00:01,000 --> 00:00:03,000\n{CONTROL}\n"


def _write(tmp_path: Path, text: str, encoding: str) -> Path:
    path = tmp_path / f"de-{encoding}.srt"
    path.write_bytes(text.encode(encoding))
    return path


def _parser():
    return get_profile("de").create_parser(switch_language(AnkiMinerConfig(), "de"))


def test_the_japanese_ladder_would_mangle_the_file(tmp_path):
    """Negative control (memory: cp932 swallows cp1252 apostrophes)."""
    path = _write(tmp_path, CONTROL_SRT, "cp1252")
    with pytest.raises(UnicodeDecodeError) as utf8_error:
        path.read_bytes().decode("utf-8")
    try:
        events = load_with_fallback_encoding(path, utf8_error.value)
    except UnicodeDecodeError:
        return
    assert CONTROL not in events[0].text


def test_cp1252_decodes_and_mines_through_the_german_parser(tmp_path):
    words = _parser().parse_subtitle_file(_write(tmp_path, SRT, "cp1252"))
    assert {"Mädchen", "schön", "Jacke"} <= {word.mined_form for word in words}
    sentence = words[0].sentence
    assert "„" in sentence and "�" not in sentence
    assert "sagte sie – wirklich!" in sentence  # contract item 18: a mid-line Gedankenstrich survives the SDH filter


def test_an_nfd_subtitle_mines_nfc_fronts(tmp_path):
    words = _parser().parse_subtitle_file(_write(tmp_path, unicodedata.normalize("NFD", SRT), "utf-8"))
    fronts = {word.mined_form for word in words}
    assert "Mädchen" in fronts and unicodedata.normalize("NFD", "Mädchen") not in fronts
    assert all(unicodedata.is_normalized("NFC", word.sentence) for word in words)


def test_the_profile_codes_pick_the_german_track(tmp_path):
    proc = MagicMock(returncode=0, stdout=FIXTURE.read_text(encoding="utf-8"), stderr="")
    with patch("anki_miner.utils.audio_track_detector.subprocess.run", return_value=proc):
        codes = get_profile("de").audio_track_codes
        assert find_japanese_audio_stream(tmp_path / "bilingual.mkv", codes=codes).language_tag == "ger"
        assert (
            find_japanese_audio_stream(tmp_path / "bilingual.mkv", codes=JAPANESE_LANGUAGE_CODES).language_tag == "jpn"
        )


@pytest.mark.parametrize(
    ("data", "native"),
    [
        ({"automatic_captions": {"de": [{}], "de-orig": [{}]}}, True),
        ({"automatic_captions": {"de": [{}], "en-orig": [{}]}}, False),
        ({"automatic_captions": {"de": [{}]}}, True),  # no -orig key and no language field: not rejected
        ({"automatic_captions": {"de": [{}]}, "language": "en"}, False),
        ({"automatic_captions": {"ja": [{}], "ja-orig": [{}]}}, False),
    ],
)
def test_caption_codes_detect_native_german(data, native):
    captions = get_profile("de").captions
    assert YouTubeFetcherService._has_native_auto_ja(data, captions=captions) is native


@pytest.mark.parametrize(("language", "present"), [("de", True), ("de-AT", True), ("deu", False), ("en", False)])
def test_the_audio_track_pattern_is_anchored(language, present):
    data = {"formats": [{"vcodec": "none", "language": language}]}
    assert YouTubeFetcherService._has_ja_audio_track(data, captions=get_profile("de").captions) is present
