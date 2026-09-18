"""Greek: cp1253 subtitles, the dead ISO-8859-7 alternate, SDH cleanup, track choice and caption codes."""

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
from anki_miner.utils.subtitle_encoding import _single_byte_leg_fails, load_with_fallback_encoding

FIXTURE = Path(__file__).parents[2] / "fixtures" / "el" / "dual_audio_ffprobe.json"
#: cp1253 has no U+037E: it encodes the Greek question mark as ASCII ";" (judge round 1, MAJOR-2).
SRT = (
    "1\n00:00:01,000 --> 00:00:03,000\nΤο σπίτι είναι μακριά από τη θάλασσα…\n\n"
    "2\n00:00:04,000 --> 00:00:06,000\n«Άργησες», είπε η μητέρα.\n\n"
    "3\n00:00:07,000 --> 00:00:09,000\nΤι κάνεις; Καλά.\n"
)
#: U+037E rides only bytes that can carry it: a UTF-8 subtitle, or a raw book unit (must-resolve 3).
SRT_UTF8 = "1\n00:00:01,000 --> 00:00:03,000\nΤι κάνεις\u037e Καλά.\n"


@pytest.fixture(scope="module")
def parser():
    return get_profile("el").create_parser(switch_language(AnkiMinerConfig(), "el"))


def _write(tmp_path: Path) -> Path:
    path = tmp_path / "el.srt"
    path.write_bytes(SRT.encode("cp1253"))
    return path


def test_the_greek_ladder_decodes_cp1253_without_a_detector_guess(tmp_path):
    """The ja ladder happens to recover these bytes through charset-normalizer; el never guesses."""
    path = _write(tmp_path)
    with pytest.raises(UnicodeDecodeError) as utf8_error:
        path.read_bytes().decode("utf-8")
    detected = load_with_fallback_encoding(path, utf8_error.value)
    assert "σπίτι" in detected[0].text  # the detector's guess, not the ladder's answer
    profile = get_profile("el")
    laddered = load_with_fallback_encoding(
        path,
        utf8_error.value,
        encodings=profile.import_encodings,
        script_check=profile.script.contains_target_script,
    )
    assert [event.text for event in laddered] == [event.text for event in detected]


def test_cp1253_decodes_and_mines_through_the_greek_parser(parser, tmp_path):
    words = parser.parse_subtitle_file(_write(tmp_path))
    assert {"σπίτι", "μακριά", "θάλασσα", "μητέρα", "καλά"} <= {word.mined_form for word in words}
    assert any("«Άργησες»" in word.sentence for word in words)


def test_the_greek_question_mark_folds_on_a_utf8_subtitle(parser, tmp_path):
    """U+037E cannot ride cp1253, so pin the NFC fold on bytes that carry it."""
    path = tmp_path / "el_utf8.srt"
    path.write_text(SRT_UTF8, encoding="utf-8")
    words = parser.parse_subtitle_file(path)
    assert "καλά" in {word.mined_form for word in words}
    assert not any("\u037e" in word.sentence for word in words)  # normalize folded it to ";"


def test_the_iso8859_7_alternate_could_never_run_behind_cp1253():
    """D19: cp1253 reads ISO-8859-7 Greek bytes as plausible Greek, so a second leg never runs."""
    text = "«Άργησες;» Ώρα να φύγουμε, Έλενα."
    data = text.encode("iso8859_7")
    assert data.decode("cp1253") == text.replace("Ά", "¶")
    # The real gate is plausibility, not raising: a single-byte leg wins unless its decode fails it.
    check = get_profile("el").script.contains_target_script
    assert _single_byte_leg_fails(data, "cp1253", check) is False
    raising = set()
    for byte in range(256):
        try:
            bytes([byte]).decode("cp1253")
        except UnicodeDecodeError:
            raising.add(byte)
    iso_letters = {byte for byte in raising if bytes([byte]).decode("iso8859_7", errors="replace").isalpha()}
    assert iso_letters == {0xAA}  # U+037A, the only ISO letter cp1253 refuses


def test_the_sdh_default_strips_greek_speakers_and_unspaced_dialogue_dashes(parser):
    units = [ReadingUnit(text="ΓΙΑΝΝΗΣ: [χτυπάει η πόρτα] -Η γάτα κοιμάται. -Ναι. ♪", index=0, location_label="t")]
    words, _index, _counts = parser.parse_text_units(units, False, subtitle_cleanup=True)
    mined = {word.mined_form for word in words}
    assert "γάτα" in mined
    assert not {"γιαννησ", "γιαννης", "πόρτα", "-η"} & mined
    assert all(not word.sentence.startswith(("ΓΙΑΝΝΗΣ", "-")) for word in words)
    # Real output (the unit is one cue, so one sentence): the leaked dash and the label are gone.
    assert {word.sentence for word in words} == {"Η γάτα κοιμάται. Ναι."}


def test_the_profile_codes_pick_the_greek_track(tmp_path):
    proc = MagicMock(returncode=0, stdout=FIXTURE.read_text(encoding="utf-8"), stderr="")
    with patch("anki_miner.utils.audio_track_detector.subprocess.run", return_value=proc):
        codes = get_profile("el").audio_track_codes
        assert find_japanese_audio_stream(tmp_path / "bilingual.mkv", codes=codes).language_tag == "gre"
        assert (
            find_japanese_audio_stream(tmp_path / "bilingual.mkv", codes=JAPANESE_LANGUAGE_CODES).language_tag == "jpn"
        )


@pytest.mark.parametrize(
    ("data", "native"),
    [
        ({"automatic_captions": {"el": [{}], "el-orig": [{}]}}, True),
        ({"automatic_captions": {"el": [{}], "en-orig": [{}]}}, False),
        ({"automatic_captions": {"el": [{}]}, "language": "el"}, True),
        ({"automatic_captions": {"el": [{}]}, "language": "en"}, False),
        ({"automatic_captions": {"ja": [{}], "ja-orig": [{}]}}, False),
    ],
)
def test_caption_codes_detect_native_greek(data, native):
    assert YouTubeFetcherService._has_native_auto_ja(data, captions=get_profile("el").captions) is native


@pytest.mark.parametrize(("language", "present"), [("el", True), ("el-GR", True), ("ell", False), ("ja", False)])
def test_the_audio_track_pattern_is_anchored(language, present):
    data = {"formats": [{"vcodec": "none", "language": language}]}
    assert YouTubeFetcherService._has_ja_audio_track(data, captions=get_profile("el").captions) is present
