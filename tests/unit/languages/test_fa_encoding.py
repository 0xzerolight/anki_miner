"""Persian: cp1256 subtitles, a bilingual file's track choice, YouTube caption codes (spec C.2).

cp1256 is where legacy Persian subtitles live, and it is a lossy home: it
carries the keheh, the ZWNJ and pe/che/zhe/gaf, but **not** the Farsi yeh. Every
file written in it therefore spells that letter the Arabic way, which is the
single biggest reason Persian needs a normalise step before anything keys on the
text. Measured, not assumed - the fixture is real cp1256 bytes.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.languages.fa.script import ZWNJ, fa_normalize
from anki_miner.languages.registry import get_profile
from anki_miner.services.youtube_fetcher import YouTubeFetcherService
from anki_miner.utils.audio_track_detector import JAPANESE_LANGUAGE_CODES, find_japanese_audio_stream
from anki_miner.utils.subtitle_encoding import load_with_fallback_encoding

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "fa"

_ARABIC_YEH = "\N{ARABIC LETTER YEH}"
_FARSI_YEH = "\N{ARABIC LETTER FARSI YEH}"
_MEEM = "\N{ARABIC LETTER MEEM}"
_REH = "\N{ARABIC LETTER REH}"
_WAW = "\N{ARABIC LETTER WAW}"
_KEHEH = "\N{ARABIC LETTER KEHEH}"
_GAF = "\N{ARABIC LETTER GAF}"
_PEH = "\N{ARABIC LETTER PEH}"

#: The smoke sentence's verb, as cp1256 must spell it and as Persian spells it.
MI_RAVAM_CP1256 = _MEEM + _ARABIC_YEH + ZWNJ + _REH + _WAW + _MEEM
MI_RAVAM = _MEEM + _FARSI_YEH + ZWNJ + _REH + _WAW + _MEEM


@pytest.fixture(scope="module")
def cp1256_events():
    profile = get_profile("fa")
    path = FIXTURES / "subtitle_cp1256.srt"
    with pytest.raises(UnicodeDecodeError) as utf8_error:
        path.read_bytes().decode("utf-8")
    # subtitle_encoding.py imports charset_normalizer.from_bytes inside the
    # detector branch; the ladder must answer before it is ever reached.
    with patch("charset_normalizer.from_bytes", side_effect=AssertionError("detector reached")):
        return load_with_fallback_encoding(
            path,
            utf8_error.value,
            encodings=profile.import_encodings,
            script_check=profile.script.contains_target_script,
        )


class TestCp1256:
    def test_the_fa_ladder_decodes_it_without_the_detector(self, cp1256_events):
        assert len(cp1256_events) == 3
        assert cp1256_events[0].text.endswith(MI_RAVAM_CP1256 + ".")

    def test_the_file_really_is_lossy_about_the_farsi_yeh(self, cp1256_events):
        """The premise: what came off disk is NOT the spelling Persian uses."""
        assert _FARSI_YEH not in cp1256_events[0].text
        assert _ARABIC_YEH in cp1256_events[0].text

    def test_normalising_restores_the_farsi_yeh(self, cp1256_events):
        assert fa_normalize(cp1256_events[0].text).endswith(MI_RAVAM + ".")
        assert _ARABIC_YEH not in fa_normalize(cp1256_events[0].text)

    def test_the_persian_only_letters_survive_the_round_trip(self, cp1256_events):
        """cp1256 has rows for keheh, gaf and pe; a wrong codec would mangle them."""
        third = fa_normalize(cp1256_events[2].text)
        assert _KEHEH in third and _GAF in third and _PEH in third

    def test_the_zwnj_survives_too(self, cp1256_events):
        """0x9d is the ZWNJ in cp1256, so the joined verb spelling arrives intact."""
        assert ZWNJ in cp1256_events[0].text

    def test_no_japanese_encoding_can_read_the_file(self):
        """The control: the fa ladder is load-bearing, not incidental.

        Measured - every encoding on the ja ladder raises on these bytes, so
        without the profile's own ``import_encodings`` a cp1256 Persian file
        reaches the charset detector or fails outright.
        """
        data = (FIXTURES / "subtitle_cp1256.srt").read_bytes()
        for encoding in get_profile("ja").import_encodings:
            with pytest.raises(UnicodeDecodeError):
                data.decode(encoding)


class TestBidiControls:
    """Subtitle editors inject RLM/LRE/PDF; the stored sentence must carry none.

    ``_strip_for_dedup`` removes them at the Anki boundary, but the sentence on
    the card comes from the parser, so the normaliser has to be the one that
    drops them - otherwise the card text and its dedup key disagree.
    """

    @pytest.mark.parametrize(
        "control",
        [
            "\N{RIGHT-TO-LEFT MARK}",
            "\N{LEFT-TO-RIGHT MARK}",
            "\N{LEFT-TO-RIGHT EMBEDDING}",
            "\N{RIGHT-TO-LEFT EMBEDDING}",
            "\N{POP DIRECTIONAL FORMATTING}",
        ],
    )
    def test_a_bidi_control_is_stripped(self, control):
        wrapped = control + MI_RAVAM + control
        assert fa_normalize(wrapped) == MI_RAVAM

    def test_the_zwnj_is_kept_although_it_is_also_a_format_character(self):
        """The one Cf character Persian needs: it is a spelling, not a control."""
        assert ZWNJ in fa_normalize(MI_RAVAM)


class TestAudioTracks:
    def test_the_profile_codes_pick_the_persian_track(self, tmp_path):
        proc = MagicMock(
            returncode=0, stdout=(FIXTURES / "dual_audio_ffprobe.json").read_text(encoding="utf-8"), stderr=""
        )
        with patch("anki_miner.utils.audio_track_detector.subprocess.run", return_value=proc):
            persian = find_japanese_audio_stream(tmp_path / "x.mkv", codes=get_profile("fa").audio_track_codes)
            japanese = find_japanese_audio_stream(tmp_path / "x.mkv", codes=JAPANESE_LANGUAGE_CODES)
        # "per" is 639-2/B, which is what Matroska and ffmpeg actually write.
        assert persian is not None and persian.language_tag == "per"
        assert japanese is not None and japanese.language_tag == "jpn"

    @pytest.mark.parametrize("code", ["per", "fas", "fa", "pes", "prs"])
    def test_every_spelling_a_rip_may_carry_is_accepted(self, code):
        assert code in get_profile("fa").audio_track_codes

    def test_arabic_is_not_a_persian_track(self):
        assert "ara" not in get_profile("fa").audio_track_codes


class TestCaptions:
    @pytest.mark.parametrize(
        ("automatic", "language", "native"),
        [
            ({"fa": [{}], "fa-orig": [{}]}, "", True),
            ({"fa": [{}]}, "", True),
            ({"fa": [{}]}, "en", False),
            ({"ar": [{}], "ar-orig": [{}]}, "", False),
        ],
    )
    def test_caption_codes_detect_native_persian(self, automatic, language, native):
        data = {"automatic_captions": automatic, "language": language}
        assert YouTubeFetcherService._has_native_auto_ja(data, captions=get_profile("fa").captions) is native

    @pytest.mark.parametrize(("language", "present"), [("fa", True), ("fa-IR", True), ("fas", False), ("ar", False)])
    def test_the_audio_track_pattern_is_anchored(self, language, present):
        data = {"formats": [{"vcodec": "none", "language": language}]}
        assert YouTubeFetcherService._has_ja_audio_track(data, captions=get_profile("fa").captions) is present

    def test_a_bare_code_still_resolves(self):
        captions = get_profile("fa").captions
        assert captions.bare_fallback is True
        assert captions.primary == "fa"
