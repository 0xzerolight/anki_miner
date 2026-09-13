"""S11: a single-byte codepage must decode to plausible text in the mining script."""

from __future__ import annotations

import codecs
import unicodedata
from pathlib import Path

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.services.known_words_import import parse_known_words_file
from anki_miner.services.reading._util import _decode
from anki_miner.utils.cjk_encoding import prefers_big5
from anki_miner.utils.subtitle_encoding import (
    _WHATWG_LABELS,
    detect_subtitle_encoding,
    load_with_fallback_encoding,
    script_check_kwarg,
)


def _latin(text: str) -> bool:
    return any("a" <= ch.lower() <= "z" for ch in text)


def _thai(text: str) -> bool:
    return any("\u0e00" <= ch <= "\u0e7f" for ch in text)


class _Script:
    def __init__(self, check):
        self.contains_target_script = check


SRT_1252 = "1\n00:00:01,000 --> 00:00:02,000\nI don’t know, café.\n\n".encode("cp1252")


def _utf8_error(data: bytes) -> UnicodeDecodeError:
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        return exc
    raise AssertionError("fixture must not be valid UTF-8")


def test_script_check_kwarg_is_empty_for_multibyte_ladders():
    script = _Script(_latin)
    assert script_check_kwarg(("utf-8-sig", "cp932", "euc_jp"), script) == {}
    assert script_check_kwarg(("utf-8-sig", "gb18030", "big5"), script) == {}
    assert script_check_kwarg(("utf-8-sig", "cp949"), script) == {}
    assert script_check_kwarg(None, script) == {}
    assert script_check_kwarg(("utf-8-sig", "cp1252"), script) == {"script_check": _latin}


def test_cp1252_subtitle_loads_through_its_validated_leg(tmp_path: Path):
    path = tmp_path / "en.srt"
    path.write_bytes(SRT_1252)

    subs = load_with_fallback_encoding(path, _utf8_error(SRT_1252), encodings=("cp1252",), script_check=_latin)

    assert subs[0].text == "I don’t know, café."
    assert detect_subtitle_encoding(path, encodings=("utf-8-sig", "cp1252"), script_check=_latin) == "windows-1252"


def test_a_leg_whose_decode_holds_none_of_the_script_is_skipped(tmp_path: Path):
    """cp874 decodes ASCII + a cp1252 curly quote without raising (0x92 is U+2019
    there too); the Thai check refuses the result, so that leg cannot name it."""
    path = tmp_path / "en.srt"
    path.write_bytes("1\n00:00:01,000 --> 00:00:02,000\nI don’t know.\n\n".encode("cp1252"))

    assert detect_subtitle_encoding(path, encodings=("utf-8-sig", "cp874")) == "windows-874"
    assert detect_subtitle_encoding(path, encodings=("utf-8-sig", "cp874"), script_check=_thai) != "windows-874"


def test_c1_controls_disqualify_a_latin_1_leg():
    raw = "don’t".encode("cp1252")  # 0x92 -> U+0092 under latin-1
    with pytest.raises(SetupError):
        _decode(raw, encodings=("latin_1",), script_check=_latin)
    assert _decode(raw, encodings=("latin_1", "cp1252"), script_check=_latin) == "don’t"


def test_a_single_byte_win_is_nfc_composed():
    # cp1258 has ê but no precomposed ế/ệ: Vietnamese text is encoded (and
    # decodes back) as base letter + combining tone mark.
    decomposed = "Ti\u00ea\u0301ng Vi\u00ea\u0323t"
    raw = codecs.encode(decomposed, "cp1258")
    text = _decode(raw, encodings=("cp1258",), script_check=_latin)
    assert text == unicodedata.normalize("NFC", decomposed) == "Tiếng Việt"


def test_known_words_ladder_validates_too(tmp_path: Path):
    path = tmp_path / "words.txt"
    path.write_bytes("café\nniño\n".encode("cp1252"))
    result = parse_known_words_file(path, encodings=("utf-8-sig", "cp1252"), script_check=_latin)
    assert {"café", "niño"} <= set(result.words)


def test_whatwg_labels_cover_the_legacy_codepages():
    expected = {
        "cp1250": "windows-1250",
        "cp1253": "windows-1253",
        "cp1254": "windows-1254",
        "cp1255": "windows-1255",
        "cp1256": "windows-1256",
        "cp1257": "windows-1257",
        "cp1258": "windows-1258",
        "koi8_r": "koi8-r",
        "koi8_u": "koi8-u",
        "iso8859_2": "iso-8859-2",
        "iso8859_6": "iso-8859-6",
        "iso8859_7": "iso-8859-7",
        "iso8859_8": "iso-8859-8",
        "iso8859_9": "windows-1254",
        "iso8859_13": "iso-8859-13",
        "iso8859_16": "iso-8859-16",
        "cp874": "windows-874",
        "tis_620": "windows-874",
        "big5hkscs": "big5",
    }
    assert {name: _WHATWG_LABELS[name] for name in expected} == expected


def test_prefers_big5_takes_the_codec_and_defaults_to_big5():
    data = "這是一個測試".encode("big5")
    assert prefers_big5(data) == prefers_big5(data, codec="big5")
    assert isinstance(prefers_big5(data, codec="big5hkscs"), bool)
