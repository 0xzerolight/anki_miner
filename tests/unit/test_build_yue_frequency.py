"""The Cantonese frequency converter, and its two duplicated constants."""

from __future__ import annotations

import importlib.util
import json
import zipfile
from collections import Counter
from pathlib import Path

import pytest

from anki_miner.services.frequency import mode_probe
from anki_miner.utils.ja_normalize import CJK_IDEOGRAPH_RANGES, is_cjk_ideograph

SCRIPT = Path(__file__).parents[2] / "scripts" / "build_yue_frequency.py"


@pytest.fixture(scope="module")
def build():
    spec = importlib.util.spec_from_file_location("build_yue_frequency", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_declared_mode_is_the_importers_literal(build):
    # A near-miss like "rank" would be read as an occurrence count and invert
    # the order, in an asset published once and never edited.
    assert build.RANK_BASED == mode_probe.RANK_BASED


def test_the_duplicated_ideograph_ranges_match_the_shared_ones(build):
    assert build.CJK_IDEOGRAPH_RANGES == CJK_IDEOGRAPH_RANGES


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("戲", True),
        ("好好睇", True),
        ("IQ題", False),
        ("Netflix", False),
        ("2024", False),
        ("，", False),
        ("", False),
    ],
)
def test_the_han_filter(build, word, expected):
    assert build.is_han(word) is expected
    if word:
        assert build.is_han(word) == all(is_cjk_ideograph(char) for char in word)


def test_counting_folds_to_nfc_and_reports_what_it_dropped(build):
    counts: Counter[str] = Counter()
    seen, kept = build.count_tokens(iter(["戲", "戲", "Netflix", "，", "好睇"]), counts)
    assert (seen, kept) == (5, 3)
    assert counts == Counter({"戲": 2, "好睇": 1})


def test_ranking_is_count_desc_then_codepoint(build):
    assert build.rank(Counter({"乙": 2, "甲": 2, "丙": 5})) == [("丙", 1), ("乙", 2), ("甲", 3)]


def test_the_zip_is_a_yomitan_frequency_dictionary(build, tmp_path):
    payload = build.build_zip([("嘅", 1), ("我", 2)], revision="2026-09-20")
    out = tmp_path / "f.zip"
    out.write_bytes(payload)
    archive = zipfile.ZipFile(out)
    index = json.loads(archive.read("index.json"))
    assert index["format"] == 3
    assert index["sourceLanguage"] == "yue"
    assert index["frequencyMode"] == "rank-based"
    assert "HKCanCor" in index["attribution"] and "CC BY 4.0" in index["attribution"]
    assert "CTCPC" in index["attribution"] and "CC0" in index["attribution"]
    assert json.loads(archive.read("term_meta_bank_1.json")) == [["嘅", "freq", 1], ["我", "freq", 2]]


def test_two_builds_of_the_same_rows_are_byte_identical(build):
    # Fixed zip timestamps: the published asset's sha256 has to be reproducible.
    first = build.build_zip([("嘅", 1)], revision="2026-09-20")
    assert first == build.build_zip([("嘅", 1)], revision="2026-09-20")


def test_the_script_imports_nothing_from_the_app():
    # It runs against a plain `pip install pycantonese` at build time, which is
    # why RANK_BASED and CJK_IDEOGRAPH_RANGES are duplicated above.
    source = SCRIPT.read_text(encoding="utf-8")
    assert "import anki_miner" not in source
    assert "from anki_miner" not in source
