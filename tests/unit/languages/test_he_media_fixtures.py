"""Hebrew: a legacy cp1255 subtitle, the track choice (``heb`` and the legacy ``iw``), captions.

Every Hebrew, pointed and invisible literal comes from ``tests/fixtures/he/encodings.json``; this
module carries none of its own (LEAD-BRIEF section 3).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.he.script import he_normalize
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.services.youtube_fetcher import YouTubeFetcherService
from anki_miner.utils.audio_track_detector import JAPANESE_LANGUAGE_CODES, find_japanese_audio_stream

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "he"
FFPROBE = FIXTURES / "dual_audio_ffprobe.json"
ENCODINGS = json.loads((FIXTURES / "encodings.json").read_text(encoding="utf-8"))
LINE = ENCODINGS["cp1255_line"]


def _srt(tmp_path: Path, text: str, codec: str) -> Path:
    path = tmp_path / f"he-{codec}.srt"
    path.write_bytes(f"1\n00:00:01,000 --> 00:00:03,000\n{text}\n".encode(codec))
    return path


# --------------------------------------------------------------------------
# Encodings (S11)
# --------------------------------------------------------------------------


def test_the_bytes_are_cp1255_and_not_utf8(tmp_path):
    data = _srt(tmp_path, LINE, "cp1255").read_bytes()
    with pytest.raises(UnicodeDecodeError):
        data.decode("utf-8")


def test_the_profile_ladder_decodes_the_file(tmp_path):
    from anki_miner.utils.subtitle_encoding import load_with_fallback_encoding

    path = _srt(tmp_path, LINE, "cp1255")
    with pytest.raises(UnicodeDecodeError) as utf8_error:
        path.read_bytes().decode("utf-8")
    profile = get_profile("he")
    assert profile.import_encodings == ("utf-8-sig", "cp1255")
    events = load_with_fallback_encoding(path, utf8_error.value, encodings=profile.import_encodings)
    assert events[0].text == LINE


def test_the_reading_ladder_decodes_the_same_file():
    """Reading -> Novels/Text/Subtitles has its OWN ladder over the same profile encodings.

    Its single-byte gate is ``plausible_single_byte_text(text, script_check)``, so a cp1255 book
    only decodes because the Hebrew script gate says the result is Hebrew.
    """
    from anki_miner.services.reading._util import _decode

    profile = get_profile("he")
    text = _decode(
        LINE.encode("cp1255"),
        encodings=profile.import_encodings,
        script_check=profile.script.contains_target_script,
    )
    assert text == LINE


def test_iso_8859_8_cannot_even_encode_a_pointed_word():
    """Why the ladder is cp1255: ISO-8859-8 is the VISUAL-order label and carries no points."""
    pointed = ENCODINGS["pointed_word"]
    with pytest.raises(UnicodeEncodeError):
        pointed.encode("iso8859_8")
    assert pointed.encode("cp1255")
    # An unpointed line does encode, which is exactly why a visual-order file decodes to
    # reversed lines instead of raising. No fold repairs that; it is a documented gap.
    assert ENCODINGS["iso8859_8_encodable"].encode("iso8859_8")


def test_the_whatwg_label_is_the_one_alass_and_the_browser_use():
    from anki_miner.utils.subtitle_encoding import _WHATWG_LABELS, is_single_byte_codec

    assert _WHATWG_LABELS["cp1255"] == "windows-1255"
    assert _WHATWG_LABELS["iso8859_8"] == "iso-8859-8"
    assert is_single_byte_codec("cp1255")


def test_the_japanese_ladder_mis_decodes_the_same_bytes(tmp_path):
    """The mojibake control: without the profile's ladder the file decodes to nonsense."""
    data = _srt(tmp_path, LINE, "cp1255").read_bytes()
    mojibake = data.decode("cp932", errors="replace")
    assert not get_profile("he").script.contains_target_script(mojibake)


def test_the_normaliser_strips_what_a_windows_subtitle_tool_injects():
    assert he_normalize(ENCODINGS["bidi_control_line"]) == ENCODINGS["expected_after_normalize"]
    assert he_normalize(ENCODINGS["nbsp_line"]) == ENCODINGS["expected_after_normalize"]


# --------------------------------------------------------------------------
# Audio tracks (R27)
# --------------------------------------------------------------------------


def _ffprobe_with(tag: str) -> str:
    """The fixture with its two Hebrew streams collapsed onto ONE tag, beside the Japanese one."""
    data = json.loads(FFPROBE.read_text(encoding="utf-8"))
    streams = [s for s in data["streams"] if s["tags"]["language"] == "jpn"]
    hebrew = next(s for s in data["streams"] if s["tags"]["language"] != "jpn")
    hebrew = {**hebrew, "tags": {**hebrew["tags"], "language": tag}}
    return json.dumps({"streams": [*streams, hebrew]})


@pytest.mark.parametrize("tag", ["heb", "he", "iw", "hebrew"])
def test_every_declared_code_picks_the_hebrew_track_on_its_own(tmp_path, tag):
    """One code per run, so each entry of ``audio_track_codes`` is load-bearing by itself.

    ``iw`` is the legacy 639-1 code an Israeli rip still writes; ``hebrew`` turns up in
    hand-tagged files. A pin that never sees a code fail asserts nothing about it.
    """
    proc = MagicMock(returncode=0, stdout=_ffprobe_with(tag), stderr="")
    with patch("anki_miner.utils.audio_track_detector.subprocess.run", return_value=proc):
        video = tmp_path / "bilingual.mkv"
        stream = find_japanese_audio_stream(video, codes=get_profile("he").audio_track_codes)
        assert stream is not None and stream.language_tag == tag
        assert find_japanese_audio_stream(video, codes=JAPANESE_LANGUAGE_CODES).language_tag == "jpn"


def test_a_code_outside_the_set_is_not_picked(tmp_path):
    """The other half: the assertion above would pass on a set that matched everything."""
    proc = MagicMock(returncode=0, stdout=_ffprobe_with("ara"), stderr="")
    with patch("anki_miner.utils.audio_track_detector.subprocess.run", return_value=proc):
        stream = find_japanese_audio_stream(tmp_path / "x.mkv", codes=get_profile("he").audio_track_codes)
    assert stream is None or stream.language_tag != "ara"


def test_the_fixture_carries_every_code_the_profile_claims_to_need():
    """A fixture missing a code makes its pin vacuous (the yue lesson)."""
    tags = {s["tags"]["language"] for s in json.loads(FFPROBE.read_text(encoding="utf-8"))["streams"]}
    assert {"heb", "iw"} <= tags, "the two codes a real Israeli rip actually writes"
    assert "jpn" in tags, "the control track the Japanese codes must still pick"


def test_an_arabic_track_is_not_hebrew(tmp_path):
    text = FFPROBE.read_text(encoding="utf-8").replace('"heb"', '"ara"').replace('"iw"', '"arz"')
    proc = MagicMock(returncode=0, stdout=text, stderr="")
    with patch("anki_miner.utils.audio_track_detector.subprocess.run", return_value=proc):
        stream = find_japanese_audio_stream(tmp_path / "x.mkv", codes=get_profile("he").audio_track_codes)
    assert stream is None or stream.language_tag not in {"ara", "arz"}


# --------------------------------------------------------------------------
# Captions
# --------------------------------------------------------------------------


def test_the_caption_codes_are_the_probed_ones():
    captions = get_profile("he").captions
    assert captions.primary == "iw"
    assert captions.codes == ("iw", "he")
    assert captions.orig_codes == ("iw-orig", "he-orig")
    assert captions.audio_pattern == "^(iw|he)(-|$)"


@pytest.mark.parametrize("code", ["iw", "he"])
def test_each_caption_code_matches_the_audio_pattern_on_its_own(code):
    """Both entries of ``codes`` are load-bearing: the pattern is what selects a dubbed track."""
    import re

    pattern = get_profile("he").captions.audio_pattern
    assert re.match(pattern, code)
    assert re.match(pattern, f"{code}-IL")
    assert not re.match(pattern, f"x{code}")
    assert not re.match(pattern, "ar")


@pytest.mark.parametrize(
    ("data", "native"),
    [
        ({"automatic_captions": {"iw": [{}], "iw-orig": [{}]}}, True),
        ({"automatic_captions": {"iw": [{}], "he-orig": [{}]}}, True),  # either -orig key counts
        ({"automatic_captions": {"iw": [{}], "en-orig": [{}]}}, False),  # machine-translated
        ({"automatic_captions": {"iw": [{}]}, "language": "iw"}, True),  # no -orig: the language decides
        ({"automatic_captions": {"iw": [{}]}, "language": "he"}, True),
        ({"automatic_captions": {"iw": [{}]}, "language": "ar"}, False),
        ({"automatic_captions": {"ja": [{}], "ja-orig": [{}]}}, False),
    ],
)
def test_caption_codes_detect_native_hebrew(data, native):
    assert YouTubeFetcherService._has_native_auto_ja(data, captions=get_profile("he").captions) is native


def test_the_detector_reads_the_forward_compatible_code_too():
    """``he`` is forward compatibility in ``codes``, and the detector reads every code.

    Five captioned Israeli videos probed with yt-dlp all exposed ``iw`` and none exposed ``he``,
    so ``primary`` stays ``iw`` — but if YouTube ever switches, a ``he``-only response is
    detected rather than reported as "no Hebrew subtitles".
    """
    he_only = {"automatic_captions": {"he": [{}], "he-orig": [{}]}}
    assert YouTubeFetcherService._has_native_auto_ja(he_only, captions=get_profile("he").captions) is True


def test_the_downloader_asks_for_the_legacy_code():
    config = switch_language(AnkiMinerConfig(), "he")
    assert config.downloader_subtitle_langs == "iw"
