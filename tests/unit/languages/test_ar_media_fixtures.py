"""Arabic: cp1256 subtitles, a bilingual file's track choice, YouTube caption codes (spec C.1)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.languages.registry import get_profile
from anki_miner.services.youtube_fetcher import YouTubeFetcherService
from anki_miner.utils.audio_track_detector import JAPANESE_LANGUAGE_CODES, find_japanese_audio_stream
from anki_miner.utils.subtitle_encoding import load_with_fallback_encoding

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "ar"


def test_the_ar_ladder_decodes_cp1256_without_the_detector():
    profile = get_profile("ar")
    path = FIXTURES / "subtitle_cp1256.srt"
    with pytest.raises(UnicodeDecodeError) as utf8_error:
        path.read_bytes().decode("utf-8")
    # subtitle_encoding.py imports charset_normalizer.from_bytes inside the detector branch.
    with patch("charset_normalizer.from_bytes", side_effect=AssertionError("detector reached")):
        events = load_with_fallback_encoding(
            path,
            utf8_error.value,
            encodings=profile.import_encodings,
            script_check=profile.script.contains_target_script,
        )
    assert (
        events[0].text
        == "\u0630\u0647\u0628 \u0627\u0644\u0637\u0627\u0644\u0628 \u0625\u0644\u0649 \u0627\u0644\u0645\u062f\u0631\u0633\u0629 \u0635\u0628\u0627\u062d\u0627\u064b."
    )  # the smoke sentence
    assert events[1].text.startswith(
        "- \u0647\u0644 \u0643\u062a\u0628\u062a \u0627\u0644\u0631\u0633\u0627\u0644\u0629\u061f"
    )


def test_the_profile_codes_pick_the_arabic_track(tmp_path):
    proc = MagicMock(returncode=0, stdout=(FIXTURES / "dual_audio_ffprobe.json").read_text(encoding="utf-8"), stderr="")
    with patch("anki_miner.utils.audio_track_detector.subprocess.run", return_value=proc):
        arabic = find_japanese_audio_stream(tmp_path / "x.mkv", codes=get_profile("ar").audio_track_codes)
        japanese = find_japanese_audio_stream(tmp_path / "x.mkv", codes=JAPANESE_LANGUAGE_CODES)
    assert arabic is not None and arabic.language_tag == "ara"
    assert japanese is not None and japanese.language_tag == "jpn"


@pytest.mark.parametrize(
    ("automatic", "language", "native"),
    [
        ({"ar": [{}], "ar-orig": [{}]}, "", True),
        ({"ar": [{}]}, "", True),
        ({"ar": [{}]}, "en", False),
        ({"fa": [{}], "fa-orig": [{}]}, "", False),
    ],
)
def test_caption_codes_detect_native_arabic(automatic, language, native):
    data = {"automatic_captions": automatic, "language": language}
    assert YouTubeFetcherService._has_native_auto_ja(data, captions=get_profile("ar").captions) is native


@pytest.mark.parametrize(("language", "present"), [("ar", True), ("ar-EG", True), ("ara", False), ("fa", False)])
def test_the_audio_track_pattern_is_anchored(language, present):
    data = {"formats": [{"vcodec": "none", "language": language}]}
    assert YouTubeFetcherService._has_ja_audio_track(data, captions=get_profile("ar").captions) is present
