"""The three Big5-family ladder sites, and that zh and ja stay byte-identical."""

from __future__ import annotations

from pathlib import Path

import pytest

from anki_miner.languages.registry import get_profile
from anki_miner.services.reading._util import _decode
from anki_miner.utils.cjk_encoding import prefers_big5
from anki_miner.utils.subtitle_encoding import (
    _WHATWG_LABELS,
    big5_family_codec,
    detect_subtitle_encoding,
    load_with_fallback_encoding,
)

FIXTURES = Path(__file__).parents[2] / "fixtures" / "yue"
YUE_LADDER = ("utf-8-sig", "gb18030", "big5hkscs")
ZH_LADDER = ("utf-8-sig", "gb18030", "big5")


def load(name: str, ladder: tuple[str, ...]):
    """Run the load path the way every caller does: after UTF-8 has failed."""
    path = FIXTURES / name
    data = path.read_bytes()
    utf8_error = UnicodeDecodeError("utf-8", data, 0, 1, "invalid start byte")
    return load_with_fallback_encoding(path, utf8_error, encodings=ladder)


def test_whatwg_folds_big5hkscs_onto_big5():
    assert _WHATWG_LABELS["big5hkscs"] == "big5"


def test_the_helper_finds_the_family_member_each_ladder_names():
    assert big5_family_codec(YUE_LADDER) == "big5hkscs"
    assert big5_family_codec(ZH_LADDER) == "big5"
    assert big5_family_codec(("utf-8-sig", "gb18030", "cp950")) == "cp950"
    assert big5_family_codec(("utf-8-sig", "cp932", "euc_jp")) is None
    assert big5_family_codec(()) is None


def test_the_colloquial_file_needs_the_hkscs_codec_to_be_preferred():
    data = (FIXTURES / "subtitle_big5hkscs.srt").read_bytes()
    assert prefers_big5(data) is False  # the default "big5" leg REFUSES HKSCS bytes
    assert prefers_big5(data, "big5hkscs") is True


# --- sites 1 and 2: the subtitle load and detect paths ----------------------


def test_a_colloquial_hk_subtitle_decodes_as_big5hkscs_not_gb18030():
    subs = load("subtitle_big5hkscs.srt", YUE_LADDER)
    assert "嘅" in subs[0].text
    assert detect_subtitle_encoding(FIXTURES / "subtitle_big5hkscs.srt", encodings=YUE_LADDER) == "big5"


def test_a_mandarin_gb18030_file_still_wins_its_leg_on_the_yue_ladder():
    subs = load("subtitle_gb18030.srt", YUE_LADDER)
    assert "电影" in subs[0].text
    assert detect_subtitle_encoding(FIXTURES / "subtitle_gb18030.srt", encodings=YUE_LADDER) == "gb18030"


# --- site 3: the Reading path -----------------------------------------------


def test_a_colloquial_hk_novel_decodes_as_big5hkscs_not_gb18030():
    raw = (FIXTURES / "novel_big5hkscs.txt").read_bytes()
    text = _decode(raw, encodings=YUE_LADDER, script_check=get_profile("yue").script.contains_target_script)
    assert "嘅" in text and "喺" in text
    # No PUA: that is what the gb18030 mis-decode looks like.
    assert not any(0xE000 <= ord(char) <= 0xF8FF for char in text)


def test_the_reading_path_still_gives_a_mandarin_novel_to_gb18030():
    raw = "我今天看了一部很好看的电影。".encode("gb18030")
    text = _decode(raw, encodings=YUE_LADDER, script_check=get_profile("yue").script.contains_target_script)
    assert "电影" in text


# --- default-identical for everyone else ------------------------------------


@pytest.mark.parametrize("name", ["subtitle_big5hkscs.srt", "subtitle_gb18030.srt"])
def test_the_zh_ladder_is_unchanged(name):
    # Both answer gb18030 on the zh ladder, which is the PRE-CHANGE answer:
    # subtitle_big5hkscs.srt carries characters plain big5 cannot represent, so
    # prefers_big5(head, "big5") returns False there exactly as it did before.
    assert detect_subtitle_encoding(FIXTURES / name, encodings=ZH_LADDER) == "gb18030"


def test_the_zh_reading_path_is_unchanged():
    raw = (FIXTURES / "subtitle_gb18030.srt").read_bytes()
    assert "电影" in _decode(raw, encodings=ZH_LADDER)


def test_the_ja_ladder_never_reaches_the_big5_branch():
    # The control: a ja ladder holds no Big5-family codec, so the guard is
    # skipped at every site and a cp932 file decodes as before.
    ladder = get_profile("ja").import_encodings
    assert big5_family_codec(ladder) is None
    subs = load("subtitle_cp932.srt", ladder)
    assert "天気" in subs[0].text
