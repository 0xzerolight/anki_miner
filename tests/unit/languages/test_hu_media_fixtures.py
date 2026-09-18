"""Hungarian: cp1250 subtitles, a bilingual file's track choice, YouTube caption codes and book sentences (E.4)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.services.reading.sentence_splitter import split_sentences
from anki_miner.services.youtube_fetcher import YouTubeFetcherService
from anki_miner.utils.audio_track_detector import JAPANESE_LANGUAGE_CODES, find_japanese_audio_stream
from anki_miner.utils.subtitle_encoding import load_with_fallback_encoding

FIXTURE = Path(__file__).parents[2] / "fixtures" / "hu" / "dual_audio_ffprobe.json"
SRT = "1\n00:00:01,000 --> 00:00:03,000\nŐszintén szólva a szél hűtötte az űrhajót.\n"


def _write(tmp_path: Path) -> Path:
    path = tmp_path / "hu.srt"
    path.write_bytes(SRT.encode("cp1250"))
    return path


def test_the_profile_declares_the_cp1250_ladder():
    assert get_profile("hu").import_encodings == ("utf-8-sig", "cp1250")


def test_the_japanese_ladder_would_mangle_the_file(tmp_path):
    """Negative control: ő (0xF5) and ű (0xFB) are different letters under the ja ladder."""
    path = _write(tmp_path)
    with pytest.raises(UnicodeDecodeError) as utf8_error:
        path.read_bytes().decode("utf-8")
    events = load_with_fallback_encoding(path, utf8_error.value)
    assert "Őszintén" not in events[0].text and "hűtötte" not in events[0].text


def test_cp1250_decodes_and_mines_through_the_hungarian_parser(tmp_path):
    parser = get_profile("hu").create_parser(switch_language(AnkiMinerConfig(), "hu"))
    words = parser.parse_subtitle_file(_write(tmp_path))
    assert {"őszinte", "szél", "űrhajó"} <= {word.mined_form for word in words}
    assert all("�" not in word.sentence and "hűtötte" in word.sentence for word in words)


def test_the_profile_codes_pick_the_hungarian_track(tmp_path):
    proc = MagicMock(returncode=0, stdout=FIXTURE.read_text(encoding="utf-8"), stderr="")
    with patch("anki_miner.utils.audio_track_detector.subprocess.run", return_value=proc):
        codes = get_profile("hu").audio_track_codes
        assert find_japanese_audio_stream(tmp_path / "bilingual.mkv", codes=codes).language_tag == "hun"
        japanese = find_japanese_audio_stream(tmp_path / "bilingual.mkv", codes=JAPANESE_LANGUAGE_CODES)
        assert japanese.language_tag == "jpn"


@pytest.mark.parametrize(
    ("automatic", "language", "native"),
    [
        ({"hu": [{}], "hu-orig": [{}]}, "", True),
        ({"hu": [{}]}, "", True),
        ({"hu": [{}]}, "de", False),
        ({"hu": [{}], "en-orig": [{}]}, "", False),
        ({"de": [{}], "de-orig": [{}]}, "", False),
    ],
)
def test_caption_codes_detect_native_hungarian(automatic, language, native):
    captions = get_profile("hu").captions
    data = {"automatic_captions": automatic, "language": language}
    assert YouTubeFetcherService._has_native_auto_ja(data, captions=captions) is native


@pytest.mark.parametrize(("language", "present"), [("hu", True), ("hu-HU", True), ("hun", False), ("de", False)])
def test_the_audio_track_pattern_is_anchored(language, present):
    data = {"formats": [{"vcodec": "none", "language": language}]}
    assert YouTubeFetcherService._has_ja_audio_track(data, captions=get_profile("hu").captions) is present


def test_book_sentences_hold_together_over_hungarian_abbreviations():
    rules = get_profile("hu").sentence_rules
    text = "Hozz gyümölcsöt, pl. almát. Dr. Kovács kb. öt percet késett. Gyere be."
    assert split_sentences(text, rules=rules) == [
        "Hozz gyümölcsöt, pl. almát.",
        "Dr. Kovács kb. öt percet késett.",
        "Gyere be.",
    ]


def test_an_ordinal_still_ends_a_book_sentence():
    """Known miss (the de 'am 3. Oktober' case): a Hungarian ordinal is a digit plus a full stop."""
    rules = get_profile("hu").sentence_rules
    assert split_sentences("A 3. emeleten lakom.", rules=rules) == ["A 3.", "emeleten lakom."]
