"""Tests for the reading-tab shared utilities."""

from anki_miner.services.reading._util import (
    JUNK_NAMES,
    is_junk_path,
    natural_sort_key,
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


def test_is_junk_path_negative():
    assert not is_junk_path("pages/001.jpg")
    assert not is_junk_path("vol/cover.png")
    assert not is_junk_path("thumbs_up.jpg")


def test_is_junk_path_backslash_separators():
    # Archive namelists use "/", but be robust to "\\" too.
    assert is_junk_path("foo\\__MACOSX\\bar.jpg")
