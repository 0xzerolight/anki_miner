"""Tests for shared subtitle encoding fallback."""

import pytest

from anki_miner.utils.subtitle_encoding import load_with_fallback_encoding


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
