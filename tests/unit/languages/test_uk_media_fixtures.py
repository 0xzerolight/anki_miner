"""Ukrainian: legacy single-byte subtitles, a trilingual file's track choice, and YouTube caption codes."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.services.youtube_fetcher import YouTubeFetcherService
from anki_miner.utils.audio_track_detector import JAPANESE_LANGUAGE_CODES, find_japanese_audio_stream
from anki_miner.utils.subtitle_encoding import load_with_fallback_encoding, script_check_kwarg

FIXTURE = Path(__file__).parents[2] / "fixtures" / "uk" / "dual_audio_ffprobe.json"
LINE = "Він прочитав цікаву книжку. Ґудзик лежить на підлозі."


def _srt(tmp_path: Path, text: str, codec: str) -> Path:
    path = tmp_path / f"uk-{codec}.srt"
    path.write_bytes(f"1\n00:00:01,000 --> 00:00:03,000\n{text}\n".encode(codec))
    return path


def _decode(path: Path, profile) -> list:
    with pytest.raises(UnicodeDecodeError) as utf8_error:
        path.read_bytes().decode("utf-8")
    return load_with_fallback_encoding(
        path,
        utf8_error.value,
        encodings=profile.import_encodings,
        **script_check_kwarg(profile.import_encodings, profile.script),
    )


def test_the_bytes_are_not_utf8_and_carry_the_ukrainian_only_letters(tmp_path):
    """cp1251 encodes і є ї ґ, which is why the ladder needs no Ukrainian-specific rung."""
    data = _srt(tmp_path, LINE, "cp1251").read_bytes()
    assert all(letter.encode("cp1251") in data for letter in ("і", "Ґ"))
    with pytest.raises(UnicodeDecodeError):
        data.decode("utf-8")


def test_the_profile_ladder_decodes_a_cp1251_file(tmp_path):
    profile = get_profile("uk")
    assert profile.import_encodings == ("utf-8-sig", "cp1251")
    assert _decode(_srt(tmp_path, LINE, "cp1251"), profile)[0].text == LINE


def test_a_koi8_u_file_is_a_documented_limit(tmp_path):
    """P11: KOI8-U's letters sit where cp1251's do, so cp1251 decodes the file without raising into
    Cyrillic mojibake that passes the Cyrillic check; the detector after the ladder never runs.

    A third rung would have to guess between two codecs that both accept the same bytes, so the
    limit is recorded rather than worked around.
    """
    profile = get_profile("uk")
    decoded = _decode(_srt(tmp_path, LINE, "koi8_u"), profile)[0].text
    assert decoded == LINE.encode("koi8_u").decode("cp1251") != LINE
    assert profile.script.contains_target_script(decoded)


@pytest.fixture(scope="module")
def parser():
    return get_profile("uk").create_parser(switch_language(AnkiMinerConfig(), "uk"))


def test_a_cp1251_file_decodes_and_mines(parser, tmp_path):
    words = parser.parse_subtitle_file(_srt(tmp_path, LINE, "cp1251"))
    assert {"прочитати", "цікавий", "книжка", "ґудзик", "лежати", "підлога"} <= {w.mined_form for w in words}


def test_the_profile_codes_pick_the_ukrainian_track(tmp_path):
    """tests/fixtures/uk/dual_audio_ffprobe.json holds ukr, eng and rus streams."""
    proc = MagicMock(returncode=0, stdout=FIXTURE.read_text(encoding="utf-8"), stderr="")
    with patch("anki_miner.utils.audio_track_detector.subprocess.run", return_value=proc):
        video = tmp_path / "trilingual.mkv"
        assert find_japanese_audio_stream(video, codes=get_profile("uk").audio_track_codes).language_tag == "ukr"
        # The same file, the Russian profile: two Cyrillic languages must not share a track.
        assert find_japanese_audio_stream(video, codes=get_profile("ru").audio_track_codes).language_tag == "rus"
        assert find_japanese_audio_stream(video, codes=JAPANESE_LANGUAGE_CODES) is None


@pytest.mark.parametrize(
    ("data", "native"),
    [
        ({"automatic_captions": {"uk": [{}], "uk-orig": [{}]}}, True),
        ({"automatic_captions": {"uk": [{}], "ru-orig": [{}]}}, False),
        ({"automatic_captions": {"uk": [{}]}, "language": "uk"}, True),
        ({"automatic_captions": {"uk": [{}]}, "language": "ru"}, False),
        ({"automatic_captions": {"ru": [{}], "ru-orig": [{}]}}, False),
    ],
)
def test_caption_codes_detect_native_ukrainian(data, native):
    assert YouTubeFetcherService._has_native_auto_ja(data, captions=get_profile("uk").captions) is native


@pytest.mark.parametrize(("language", "present"), [("uk", True), ("uk-UA", True), ("ukr", False), ("ru", False)])
def test_the_audio_track_pattern_is_anchored(language, present):
    data = {"formats": [{"vcodec": "none", "language": language}]}
    assert YouTubeFetcherService._has_ja_audio_track(data, captions=get_profile("uk").captions) is present
