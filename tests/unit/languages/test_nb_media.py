"""Norwegian Bokmål: cp1252 subtitles decode with æøå and curly quotes intact and mine through the parser (E.4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.utils.subtitle_encoding import load_with_fallback_encoding

SRT = "1\n00:00:01,000 --> 00:00:03,000\nBlåbærsyltetøy på brødskiva, sa hun «bare» – ‘ja’.\n"


def _write(tmp_path: Path) -> Path:
    path = tmp_path / "nb.srt"
    path.write_bytes(SRT.encode("cp1252"))
    return path


def test_the_japanese_ladder_would_mangle_the_file(tmp_path):
    """Negative control: without the nb ladder æ/ø/å do not survive (observed: Blĺbćrsyltetřy)."""
    path = _write(tmp_path)
    with pytest.raises(UnicodeDecodeError) as utf8_error:
        path.read_bytes().decode("utf-8")
    events = load_with_fallback_encoding(path, utf8_error.value)
    assert "Blåbærsyltetøy" not in events[0].text


def test_cp1252_decodes_and_mines_through_the_norwegian_parser(tmp_path):
    parser = get_profile("nb").create_parser(switch_language(AnkiMinerConfig(), "nb"))
    words = parser.parse_subtitle_file(_write(tmp_path))
    assert {"blåbærsyltetøy", "brødskive", "si", "bare"} <= {word.mined_form for word in words}
    assert all("�" not in word.sentence and "«bare» – ‘ja’" in word.sentence for word in words)
