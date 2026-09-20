"""The vi frequency converter over 60 real OpenSubtitles 2024 cues, through the app's own tokenizer."""

from __future__ import annotations

import gzip
import importlib.util
import json
import sys
import unicodedata
import zipfile
from collections import Counter
from pathlib import Path

import pytest

from anki_miner.languages.token import LanguageToken
from anki_miner.languages.vi.keys import vi_fold_term
from anki_miner.languages.vi.tokenizer import build_tagger

ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = ROOT / "scripts" / "build_vi_frequency.py"
_spec = importlib.util.spec_from_file_location("build_vi_frequency", _SCRIPT)
assert _spec is not None and _spec.loader is not None
bvf = importlib.util.module_from_spec(_spec)
sys.modules["build_vi_frequency"] = bvf
_spec.loader.exec_module(bvf)

SAMPLE = ROOT / "tests" / "fixtures" / "vi" / "opensubtitles2024_sample.txt"


@pytest.fixture(scope="module")
def counts():
    lines = SAMPLE.read_text(encoding="utf-8").splitlines()
    return bvf.count_lines(build_tagger(), lines)


@pytest.mark.parametrize(
    ("token", "key"),
    [
        (LanguageToken("Hoà bình", "N", lemma="hòa bình"), "hòa bình"),
        (LanguageToken("Anh", "N", "stopword", lemma="anh"), "anh"),  # stopwords are real, frequent words
        (LanguageToken("Jolene Parker", "Np", lemma="jolene parker"), None),
        (LanguageToken(".", "CH", lemma="."), None),
        (LanguageToken("01:10", "M", lemma="01:10"), None),
        (LanguageToken("F1", "N", lemma="f1"), None),
        (LanguageToken("♪", "X", lemma="♪"), None),
    ],
)
def test_the_key_is_the_folded_front_and_names_punctuation_digits_are_dropped(token, key):
    assert bvf.frequency_key(token) == key


def test_the_sample_counts_are_folded_words(counts):
    assert counts and all(key == vi_fold_term(key) for key in counts)
    assert all(unicodedata.is_normalized("NFC", key) and not any(c.isdigit() for c in key) for key in counts)
    assert counts["tôi"] >= 5 and counts["chồng"] >= 2
    assert not {"jolene parker", "elizabeth keen", ".", "?"} & set(counts)


def test_ranks_are_dense_ordered_and_capped():
    rows = bvf.rank_rows(Counter({"tôi": 9, "là": 9, "anh": 3, "bác sĩ": 1}), cap=3)
    assert rows == [("là", 1), ("tôi", 2), ("anh", 3)]  # ties broken by the term, deterministic


def test_the_written_zip_is_a_rank_based_yomitan_frequency_dictionary(tmp_path):
    out = tmp_path / "opensubtitles-vi-word.zip"
    rows = [(f"từ{i}", i + 1) for i in range(12_345)]
    bvf.write_yomitan(rows, out, revision="test", lines=100)
    first = out.read_bytes()
    bvf.write_yomitan(rows, out, revision="test", lines=100)
    assert out.read_bytes() == first  # reproducible bytes, reproducible sha256
    with zipfile.ZipFile(out) as zf:
        assert zf.namelist() == ["index.json", "term_meta_bank_1.json", "term_meta_bank_2.json"]
        index = json.loads(zf.read("index.json"))
        bank = json.loads(zf.read("term_meta_bank_2.json"))
    assert (index["format"], index["frequencyMode"], index["sourceLanguage"]) == (3, "rank-based", "vi")
    assert "ODC-BY" in index["attribution"] and index["revision"] == "test"
    assert bank[0] == ["từ10000", "freq", 10_001] and len(bank) == 2_345


def test_the_app_imports_the_asset_and_reads_a_rank(tmp_path, counts):
    from anki_miner.languages.registry import get_profile
    from anki_miner.services.frequency.providers.indexed_freq_provider import IndexedFreqProvider
    from anki_miner.services.frequency.source_importer import import_frequency_source

    out = tmp_path / "opensubtitles-vi-word.zip"
    rows = bvf.rank_rows(counts)
    bvf.write_yomitan(rows, out, revision="test", lines=60)
    result = import_frequency_source(out, tmp_path / "freqs", source_id="opensubtitles-vi-word", language="vi")
    assert result.entry_count == len(rows)
    provider = IndexedFreqProvider(
        "opensubtitles-vi-word",
        tmp_path / "freqs" / "opensubtitles-vi-word" / "index.sqlite",
        "OpenSubtitles 2024 (vi)",
        keys=get_profile("vi").dict_keys,
    )
    assert provider.load()
    rank = provider.lookup("tôi")
    assert rank is not None and 1 <= rank <= 10
    assert provider.lookup("Tôi") == rank  # the query folds like the key
    provider.close()


def test_the_cli_reads_a_gz_corpus_in_process(tmp_path):
    corpus = tmp_path / "vi.txt.gz"
    with gzip.open(corpus, "wt", encoding="utf-8") as handle:
        handle.write(SAMPLE.read_text(encoding="utf-8"))
    out = tmp_path / "asset.zip"
    assert bvf.main([str(corpus), str(out), "--revision", "test", "--processes", "1", "--limit", "20"]) == 0
    with zipfile.ZipFile(out) as zf:
        assert json.loads(zf.read("index.json"))["description"].startswith("20 lines")
