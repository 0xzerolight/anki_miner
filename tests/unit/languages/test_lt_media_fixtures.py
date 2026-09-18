"""Lithuanian: a real cp1257 subtitle file, the SDH default, track choice and YouTube caption codes."""

from __future__ import annotations

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

FIXTURES = Path(__file__).parents[2] / "fixtures" / "lt"
CP1257_SRT = FIXTURES / "subtitle_cp1257.srt"
FFPROBE = FIXTURES / "dual_audio_ffprobe.json"
#: Fronts the real model gets right over the fixture's own cues. The rest of its output is the weak-model
#: miss D-4 records and never asserted (``mokytoti``, ``skaisti``, ``taryti`` are fabricated lemmas).
LT_EXPECTED_FRONTS = {"knyga", "stalas", "vaikas", "filmas"}


@pytest.fixture(scope="module")
def parser():
    return get_profile("lt").create_parser(switch_language(AnkiMinerConfig(), "lt"))


def test_the_committed_fixture_really_is_cp1257():
    data = CP1257_SRT.read_bytes()
    with pytest.raises(UnicodeDecodeError):
        data.decode("utf-8")
    assert "knyga" in data.decode("cp1257").casefold()


def test_the_profile_ladder_decodes_it_without_the_detector(tmp_path):
    data = CP1257_SRT.read_bytes()
    with pytest.raises(UnicodeDecodeError) as utf8_error:
        data.decode("utf-8")
    path = tmp_path / "lt.srt"
    path.write_bytes(data)
    profile = get_profile("lt")
    events = load_with_fallback_encoding(
        path,
        utf8_error.value,
        encodings=profile.import_encodings,
        script_check=profile.script.contains_target_script,
    )
    assert any("knyg" in event.text.casefold() for event in events)


def test_cp1257_decodes_and_mines_through_the_lithuanian_parser(parser, tmp_path):
    path = tmp_path / "lt.srt"
    path.write_bytes(CP1257_SRT.read_bytes())
    words = parser.parse_subtitle_file(path)
    assert {word.mined_form for word in words} >= LT_EXPECTED_FRONTS


def test_the_sdh_default_strips_lithuanian_speaker_labels(parser):
    units = [ReadingUnit(text="ŠARŪNAS: Katė miega. - Taip, miega. [durys] ♪", index=0, location_label="t")]
    words, _index, _counts = parser.parse_text_units(units, False, subtitle_cleanup=True)
    mined = {word.mined_form for word in words}
    assert "katė" in mined
    assert not {"šarūnas", "durys"} & mined
    assert all(not word.sentence.startswith(("ŠARŪNAS", "-")) for word in words)


def test_a_dash_after_a_deleted_sound_effect_is_a_documented_miss(parser):
    """The shared Latin dash rule fires at the cue start or after a terminator, not after a deleted span.

    el built a Greek-only lookbehind for this; Lithuanian keeps the shared rule (D-6) because no Lithuanian
    material in the survey pairs a bracketed sound effect with a dash inside one cue. The word is still mined;
    only the leading ``- `` survives in the card sentence.
    """
    units = [ReadingUnit(text="[durys] - Katė miega.", index=0, location_label="t")]
    words, _index, _counts = parser.parse_text_units(units, False, subtitle_cleanup=True)
    assert {word.mined_form for word in words} == {"katė", "miega"}
    assert {word.sentence for word in words} == {"- Katė miega."}


def test_the_profile_codes_pick_the_lithuanian_track(tmp_path):
    proc = MagicMock(returncode=0, stdout=FFPROBE.read_text(encoding="utf-8"), stderr="")
    with patch("anki_miner.utils.audio_track_detector.subprocess.run", return_value=proc):
        codes = get_profile("lt").audio_track_codes
        assert find_japanese_audio_stream(tmp_path / "bilingual.mkv", codes=codes).language_tag == "lit"
        assert (
            find_japanese_audio_stream(tmp_path / "bilingual.mkv", codes=JAPANESE_LANGUAGE_CODES).language_tag == "jpn"
        )


@pytest.mark.parametrize(
    ("data", "native"),
    [
        ({"automatic_captions": {"lt": [{}], "lt-orig": [{}]}}, True),
        ({"automatic_captions": {"lt": [{}], "en-orig": [{}]}}, False),
        ({"automatic_captions": {"lt": [{}]}, "language": "lt"}, True),
        ({"automatic_captions": {"lt": [{}]}, "language": "en"}, False),
        ({"automatic_captions": {"ja": [{}], "ja-orig": [{}]}}, False),
    ],
)
def test_caption_codes_detect_native_lithuanian(data, native):
    assert YouTubeFetcherService._has_native_auto_ja(data, captions=get_profile("lt").captions) is native


@pytest.mark.parametrize(("language", "present"), [("lt", True), ("lt-LT", True), ("lit", False), ("ja", False)])
def test_the_audio_track_pattern_is_anchored(language, present):
    data = {"formats": [{"vcodec": "none", "language": language}]}
    assert YouTubeFetcherService._has_ja_audio_track(data, captions=get_profile("lt").captions) is present
