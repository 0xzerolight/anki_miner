"""Tests for shared subtitle encoding fallback."""

import pytest

from anki_miner.utils.subtitle_encoding import detect_subtitle_encoding, load_with_fallback_encoding


@pytest.mark.parametrize("encoding", ["utf-16", "utf-32"])
def test_unicode_bom_bypasses_cp932_empty_parse(tmp_path, encoding):
    data = "1\r\n00:00:01,000 --> 00:00:03,000\r\n猫\r\n\r\n".encode(encoding)
    data.decode("cp932")  # Regression precondition: cp932 accepts these bytes.
    path = tmp_path / f"{encoding}.srt"
    path.write_bytes(data)
    utf8_error = UnicodeDecodeError("utf-8", data, 0, 1, "invalid start byte")

    subs = load_with_fallback_encoding(path, utf8_error)

    assert len(subs) == 1
    assert subs[0].text == "猫"


# ---------------------------------------------------------------------------
# detect_subtitle_encoding — names an encoding for an external consumer (alass)
# ---------------------------------------------------------------------------

_SRT = "1\r\n00:00:01,000 --> 00:00:03,000\r\n猫\r\n\r\n"


def test_utf8_detected(tmp_path):
    path = tmp_path / "a.srt"
    path.write_text(_SRT, encoding="utf-8")
    assert detect_subtitle_encoding(path) == "utf-8"


def test_cp932_named_with_the_whatwg_label(tmp_path):
    """alass panics on the Python codec name; it must be told ``shift_jis``."""
    path = tmp_path / "a.srt"
    path.write_bytes(_SRT.encode("cp932"))
    assert detect_subtitle_encoding(path) == "shift_jis"


def test_euc_jp_decodes_as_japanese_and_uses_whatwg_label(tmp_path):
    text = "1\r\n00:00:01,000 --> 00:00:03,000\r\n猫が走る\r\n\r\n"
    data = text.encode("euc_jp")
    path = tmp_path / "euc-jp.srt"
    path.write_bytes(data)
    with pytest.raises(UnicodeDecodeError) as exc_info:
        data.decode("utf-8")

    subs = load_with_fallback_encoding(path, exc_info.value)
    detected = detect_subtitle_encoding(path)

    assert subs[0].text == "猫が走る"
    assert detected == "euc-jp"
    assert detected != "euc-kr"


@pytest.mark.parametrize(
    ("encoding", "expected"),
    [("utf-16-le", "utf-16le"), ("utf-16-be", "utf-16be")],
)
def test_utf16_bom_resolves_to_the_right_endianness(tmp_path, encoding, expected):
    path = tmp_path / "a.srt"
    # The explicit-endianness codecs omit the BOM; prepend it, since the BOM is
    # what detection reads.
    bom = b"\xff\xfe" if expected == "utf-16le" else b"\xfe\xff"
    path.write_bytes(bom + _SRT.encode(encoding))
    assert detect_subtitle_encoding(path) == expected


def test_utf32_is_unnameable(tmp_path):
    """WHATWG has no UTF-32 label, so nothing is declared and alass detects."""
    path = tmp_path / "a.srt"
    path.write_bytes(_SRT.encode("utf-32"))
    assert detect_subtitle_encoding(path) is None


def test_missing_file_is_unnameable(tmp_path):
    assert detect_subtitle_encoding(tmp_path / "nope.srt") is None
