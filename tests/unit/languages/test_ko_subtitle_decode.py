"""A cp949 Korean .srt decodes through the ko ladder, not the Japanese one."""

from __future__ import annotations

from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.utils.subtitle_encoding import load_with_fallback_encoding

pytest.importorskip("kiwipiepy")

_SRT = "1\n00:00:01,000 --> 00:00:03,000\n한국어 학생입니다.\n"
#: Kana and halfwidth katakana — the shape a cp932 leg leaves behind. These
#: bytes happen to land on kanji (廃厩嬢 俳持脊艦陥), which the ko script gate
#: accepts as hanja, so the assertion with the teeth is ``"학생" in forms``;
#: this range check is the belt for a fixture whose mojibake is kana.
_MOJIBAKE_RANGES = (("぀", "ヿ"), ("｡", "ﾟ"))


def _write(tmp_path: Path) -> Path:
    path = tmp_path / "ko.srt"
    path.write_bytes(_SRT.encode("cp949"))
    return path


def test_ko_ladder_is_declared_by_the_profile() -> None:
    assert get_profile("ko").import_encodings == ("utf-8-sig", "cp949")


def test_the_japanese_ladder_would_not_yield_korean(tmp_path) -> None:
    """Negative control: this is what the file decodes to WITHOUT the ko ladder.

    cp932 accepts these bytes without raising and produces mojibake (here
    廃厩嬢 俳持脊艦陥), which is exactly why the ladder has to come from the
    profile: silent wrong text, not an exception the caller could catch.
    """
    path = _write(tmp_path)
    with pytest.raises(UnicodeDecodeError) as utf8_error:
        path.read_bytes().decode("utf-8")
    try:
        mangled = load_with_fallback_encoding(path, utf8_error.value)  # no encodings -> ja ladder
    except UnicodeDecodeError:
        return  # the ja ladder refusing the file outright is also "not Korean"
    assert "학생" not in mangled[0].text


def test_cp949_srt_decodes_and_mines_through_the_ko_parser(tmp_path: Path) -> None:
    # switch_language, not replace(language="ko"): the POS gate reads
    # config.allowed_pos, and only the switch swaps in the profile's Sejong
    # scoped_defaults. A bare replace leaves the JA tags and mines nothing,
    # which would fail this test for a reason that is not the decode ladder.
    parser = get_profile("ko").create_parser(switch_language(AnkiMinerConfig(), "ko"))
    forms = [w.mined_form for w in parser.parse_subtitle_file(_write(tmp_path))]
    assert "학생" in forms
    joined = "".join(forms)
    # Assert the mojibake is ABSENT, not merely that hangul is present.
    assert not any(low <= ch <= high for ch in joined for low, high in _MOJIBAKE_RANGES)
    assert "�" not in joined
