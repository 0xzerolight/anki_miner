"""Tests for shared subtitle encoding fallback."""

from pathlib import Path

import pytest

from anki_miner.utils import subtitle_encoding as encoding_mod
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


def test_euc_jp_probe_reads_only_the_bounded_head(tmp_path, monkeypatch):
    """The euc-jp check inside load_with_fallback_encoding must not read the
    whole file to decide — it reuses the same bounded head the cp932/detector
    legs use, never a fresh path.read_bytes()."""
    monkeypatch.setattr(encoding_mod, "_MAX_SNIFF_BYTES", 64)
    text = "1\r\n00:00:01,000 --> 00:00:03,000\r\n猫が走る\r\n\r\n"
    data = text.encode("euc_jp")
    path = tmp_path / "big-euc-jp.srt"
    path.write_bytes(data)
    with pytest.raises(UnicodeDecodeError) as exc_info:
        data.decode("utf-8")

    read_sizes: list[int] = []
    real_open = Path.open

    def _spy_open(self, *args, **kwargs):
        fh = real_open(self, *args, **kwargs)
        real_read = fh.read

        def _read(n=-1):
            read_sizes.append(n)
            return real_read(n)

        fh.read = _read
        return fh

    monkeypatch.setattr(Path, "open", _spy_open)

    subs = load_with_fallback_encoding(path, exc_info.value)

    assert subs[0].text == "猫が走る"
    # pysubs2's own file access (the cp932 attempt, then the winning euc_jp
    # parse) reads the whole file unbounded (n=-1) — that's pysubs2 actually
    # parsing the subtitle, not sniffing, and out of scope here. What matters
    # is that the euc-jp CHECK itself used the bounded head, not its own
    # fresh whole-file read.
    assert 64 in read_sizes
    assert read_sizes.count(-1) <= 2  # the two pysubs2.load attempts only


def test_sniff_bound_caps_the_read(tmp_path, monkeypatch):
    """A user-picked huge file only ever gets a bounded head read, not a whole-file
    slurp — the sniff heuristics run on ``_MAX_SNIFF_BYTES`` bytes, never more."""
    monkeypatch.setattr(encoding_mod, "_MAX_SNIFF_BYTES", 1024)
    path = tmp_path / "big.srt"
    path.write_bytes(_SRT.encode("utf-8") + b"a" * (5 * 1024 * 1024))

    read_sizes: list[int] = []
    real_open = Path.open

    def _spy_open(self, *args, **kwargs):
        fh = real_open(self, *args, **kwargs)
        real_read = fh.read

        def _read(n=-1):
            read_sizes.append(n)
            return real_read(n)

        fh.read = _read
        return fh

    monkeypatch.setattr(Path, "open", _spy_open)

    assert detect_subtitle_encoding(path) == "utf-8"
    assert read_sizes == [1024]


def test_sniff_bound_trims_truncated_multibyte_tail(tmp_path, monkeypatch):
    """A bounded read that lands mid multi-byte UTF-8 character must not be
    misreported as undetectable — the truncated tail is trimmed before the
    decode attempt, so this never falls through to charset-normalizer's
    unbounded from_path/from_bytes call on the rest of a huge file."""
    monkeypatch.setattr(encoding_mod, "_MAX_SNIFF_BYTES", 99)
    path = tmp_path / "big.srt"
    data = ("1\r\n00:00:01,000 --> 00:00:03,000\r\n" + "猫" * 1000 + "\r\n\r\n").encode("utf-8")
    path.write_bytes(data)
    # Sanity: byte 99 really does land inside a 3-byte character — otherwise
    # this test wouldn't exercise the trim at all.
    with pytest.raises(UnicodeDecodeError):
        data[:99].decode("utf-8")

    read_sizes: list[int] = []
    real_open = Path.open

    def _spy_open(self, *args, **kwargs):
        fh = real_open(self, *args, **kwargs)
        real_read = fh.read

        def _read(n=-1):
            read_sizes.append(n)
            return real_read(n)

        fh.read = _read
        return fh

    monkeypatch.setattr(Path, "open", _spy_open)

    assert detect_subtitle_encoding(path) == "utf-8"
    assert read_sizes == [99]  # one bounded read only — no whole-file fallback


def test_sniff_bound_large_enough_for_normal_files(tmp_path):
    """A real subtitle file — far smaller than the sniff bound — detects unchanged."""
    text = "1\r\n00:00:01,000 --> 00:00:03,000\r\n猫が走る\r\n\r\n" * 500
    path = tmp_path / "normal.srt"
    path.write_bytes(text.encode("cp932"))
    assert detect_subtitle_encoding(path) == "shift_jis"
