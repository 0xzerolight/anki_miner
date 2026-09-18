"""Danish: cp1252 subtitles, a bilingual file's track choice, YouTube caption codes, the unspaced dash (E.4)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.services.youtube_fetcher import YouTubeFetcherService
from anki_miner.utils.audio_track_detector import JAPANESE_LANGUAGE_CODES, find_japanese_audio_stream

FIXTURE = Path(__file__).parents[2] / "fixtures" / "da" / "dual_audio_ffprobe.json"
SRT = "1\n00:00:01,000 --> 00:00:03,000\n" "Hun købte blåbærsyltetøj og øllet, sagde »bare« – ‘ja’.\n"
DASH_SRT = "1\n00:00:01,000 --> 00:00:03,000\n-Bogen ligger her.\n-Nej, jeg vil sove.\n"


def _write(tmp_path: Path, text: str = SRT) -> Path:
    path = tmp_path / "da.srt"
    path.write_bytes(text.encode("cp1252"))
    return path


def test_the_japanese_rung_would_mangle_the_file(tmp_path):
    """Negative control: cp932, the ja ladder's first rung, eats the Danish vowels.

    The ja ladder rejects cp932 for this line and its charset-normalizer leg happens to rescue it; the da
    ``import_encodings`` entry is what makes the decode deterministic rather than detector luck.
    """
    raw = _write(tmp_path).read_bytes()
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")
    assert "blåbærsyltetøj" not in raw.decode("cp932", errors="replace")
    assert get_profile("da").import_encodings == ("utf-8-sig", "cp1252")


def test_cp1252_decodes_and_mines_through_the_danish_parser(tmp_path):
    parser = get_profile("da").create_parser(switch_language(AnkiMinerConfig(), "da"))
    words = parser.parse_subtitle_file(_write(tmp_path))
    assert {"blåbærsyltetøj", "købe"} <= {word.mined_form for word in words}
    assert all("�" not in word.sentence for word in words)
    assert any("»bare« – ‘ja’" in word.sentence for word in words)


def test_an_unspaced_speaker_dash_does_not_eat_the_first_word(tmp_path):
    """DA21: Danish subtitles write -Bogen unspaced; the da subtitle_regex takes it, the shared Latin one cannot."""
    parser = get_profile("da").create_parser(switch_language(AnkiMinerConfig(), "da"))
    fronts = {word.mined_form for word in parser.parse_subtitle_file(_write(tmp_path, DASH_SRT))}
    assert {"bog", "sove"} <= fronts and "-bog" not in fronts


def test_the_profile_codes_pick_the_danish_track(tmp_path):
    proc = MagicMock(returncode=0, stdout=FIXTURE.read_text(encoding="utf-8"), stderr="")
    with patch("anki_miner.utils.audio_track_detector.subprocess.run", return_value=proc):
        codes = get_profile("da").audio_track_codes
        assert find_japanese_audio_stream(tmp_path / "bilingual.mkv", codes=codes).language_tag == "dan"
        japanese = find_japanese_audio_stream(tmp_path / "bilingual.mkv", codes=JAPANESE_LANGUAGE_CODES)
        assert japanese.language_tag == "jpn"


@pytest.mark.parametrize(
    ("automatic", "language", "native"),
    [
        ({"da": [{}], "da-orig": [{}]}, "", True),
        # No -orig key at all proves nothing (its registration is conditional): the language field decides.
        ({"da": [{}]}, "", True),
        ({"da": [{}]}, "sv", False),
        # Another language's -orig names a non-Danish original: the bare da track is a translation.
        ({"da": [{}], "en-orig": [{}]}, "", False),
        ({"sv": [{}], "sv-orig": [{}]}, "", False),
    ],
)
def test_caption_codes_detect_native_danish(automatic, language, native):
    """The fetcher's real rule (youtube_fetcher._has_native_auto_ja), read with the Danish captions."""
    captions = get_profile("da").captions
    data = {"automatic_captions": automatic, "language": language}
    assert YouTubeFetcherService._has_native_auto_ja(data, captions=captions) is native


@pytest.mark.parametrize(("language", "present"), [("da", True), ("da-DK", True), ("dan", False), ("sv", False)])
def test_the_audio_track_pattern_is_anchored(language, present):
    data = {"formats": [{"vcodec": "none", "language": language}]}
    assert YouTubeFetcherService._has_ja_audio_track(data, captions=get_profile("da").captions) is present
