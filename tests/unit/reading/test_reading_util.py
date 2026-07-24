"""Tests for the reading-tab shared utilities."""

import zipfile

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.services.reading._util import (
    JUNK_NAMES,
    _decode,
    is_junk_path,
    natural_sort_key,
    read_zip_member_text_capped,
)


def test_natural_sort_orders_numerically():
    names = ["Vol10", "Vol2", "Vol1", "Vol20"]
    assert sorted(names, key=natural_sort_key) == ["Vol1", "Vol2", "Vol10", "Vol20"]


def test_natural_sort_vol2_before_vol10():
    assert natural_sort_key("Vol2") < natural_sort_key("Vol10")


def test_natural_sort_mixed_chunks():
    # Digit runs int-cast, text kept as-is (classic natural sort).
    assert natural_sort_key("ch3-p12") == ["ch", 3, "-p", 12, ""]


def test_junk_names_is_frozenset():
    assert isinstance(JUNK_NAMES, frozenset)


def test_is_junk_path_positive():
    assert is_junk_path(".DS_Store")
    assert is_junk_path("Thumbs.db")
    assert is_junk_path("__MACOSX/cover.jpg")
    assert is_junk_path("foo/__MACOSX/bar.jpg")  # nested component
    assert is_junk_path("pages/.DS_Store")
    assert is_junk_path("$RECYCLE.BIN/x")
    assert is_junk_path("._Book.epub")  # macOS AppleDouble sidecar
    assert is_junk_path("folder/._Vol1.epub")


def test_is_junk_path_negative():
    assert not is_junk_path("pages/001.jpg")
    assert not is_junk_path("vol/cover.png")
    assert not is_junk_path("thumbs_up.jpg")


def test_is_junk_path_backslash_separators():
    # Archive namelists use "/", but be robust to "\\" too.
    assert is_junk_path("foo\\__MACOSX\\bar.jpg")


def test_decode_lives_in_util():
    # Canonical home after the aozora extraction (shared with subtitle_source);
    # deep coverage stays in test_aozora_source via the re-export.
    assert _decode("日本語".encode()) == "日本語"
    assert _decode("日本語".encode("cp932")) == "日本語"


def _zip_with(path, members: dict[str, bytes]):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def test_read_zip_member_text_capped_happy_path(tmp_path):
    archive = _zip_with(tmp_path / "vol.cbz", {"vol.mokuro": '{"title": "日本"}'.encode()})
    assert read_zip_member_text_capped(archive, "vol.mokuro", 1024, ".mokuro member") == '{"title": "日本"}'


def test_read_zip_member_text_capped_over_cap_declared_size(tmp_path):
    archive = _zip_with(tmp_path / "vol.cbz", {"vol.mokuro": b"x" * 64})
    with pytest.raises(SetupError, match="cap"):
        read_zip_member_text_capped(archive, "vol.mokuro", 16, ".mokuro member")


def test_read_zip_member_text_capped_missing_member(tmp_path):
    archive = _zip_with(tmp_path / "vol.cbz", {"other.txt": b"hi"})
    with pytest.raises(SetupError, match="vol.mokuro"):
        read_zip_member_text_capped(archive, "vol.mokuro", 1024, ".mokuro member")


def test_read_zip_member_text_capped_bad_zip(tmp_path):
    corrupt = tmp_path / "vol.cbz"
    corrupt.write_bytes(b"PK\x03\x04 not a real zip")
    with pytest.raises(SetupError, match="vol.cbz"):
        read_zip_member_text_capped(corrupt, "vol.mokuro", 1024, ".mokuro member")


def test_read_zip_member_text_capped_non_utf8(tmp_path):
    archive = _zip_with(tmp_path / "vol.cbz", {"vol.mokuro": "日本".encode("cp932")})
    with pytest.raises(SetupError, match="vol.mokuro"):
        read_zip_member_text_capped(archive, "vol.mokuro", 1024, ".mokuro member")
