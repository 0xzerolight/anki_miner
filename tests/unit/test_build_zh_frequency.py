"""The zh frequency converter over 60 real OpenSubtitles 2024 cues, through the app's own tokenizer."""

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

from anki_miner.languages.zh.tokenizer import build_tagger
from anki_miner.languages.zh.variants import script_key
from anki_miner.utils.ja_normalize import is_cjk_ideograph

ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = ROOT / "scripts" / "build_zh_frequency.py"
_spec = importlib.util.spec_from_file_location("build_zh_frequency", _SCRIPT)
assert _spec is not None and _spec.loader is not None
bzf = importlib.util.module_from_spec(_spec)
sys.modules["build_zh_frequency"] = bzf
_spec.loader.exec_module(bzf)

SAMPLE = ROOT / "tests" / "fixtures" / "zh" / "opensubtitles2024_sample.txt"


@pytest.fixture(scope="module")
def counts():
    lines = SAMPLE.read_text(encoding="utf-8").splitlines()
    return bzf.count_lines(build_tagger(), lines)


@pytest.mark.parametrize(
    ("surface", "key"),
    [
        ("医院", "医院"),
        ("什麼", "什么"),  # the traditional cue counts under the simplified front
        ("華盛頓", "华盛顿"),  # a name is a word here: jieba's ns is not underthesea's Np
        ("的", "的"),  # function words are real words; the list ranks words, not fronts
        ("麵", "麵"),  # script_key refuses the lossy fold, so noodles stay out of 面
        ("卡拉OK", "卡拉OK"),  # one ideograph is enough: the gate is "contains", not "all"
        ("Lizzy", None),
        ("...", None),
        ("，", None),
        ("40", None),
    ],
)
def test_the_key_is_the_folded_front_and_han_less_tokens_are_dropped(surface, key):
    assert bzf.frequency_key(surface) == key


def test_a_key_is_exactly_the_front_a_simplified_run_mines():
    # The whole point of re-segmenting: a rank is found without a fallback because
    # the key and the card front come out of the same fold.
    from anki_miner.languages.zh.support import ZhMinedFormPolicy

    policy = ZhMinedFormPolicy("simplified")
    for surface in ("什麼", "醫院", "這裏", "华盛顿", "麵"):
        assert bzf.frequency_key(surface) == policy.mined_form("n", surface, surface, surface)


def test_the_sample_counts_are_folded_han_keys(counts):
    assert counts
    assert all(key == script_key(key) and unicodedata.is_normalized("NFC", key) for key in counts)
    assert all(any(is_cjk_ideograph(char) for char in key) for key in counts)
    assert not {"Lizzy", "GPS", "40", "，"} & set(counts)


def test_the_two_scripts_count_as_one_word(counts):
    # 我们/我們 and 这里/這裏 both appear in the sample; a key is only ever the folded spelling.
    assert counts["我们"] >= 6 and counts["这里"] >= 3
    assert not {"我們", "這裏", "什麼"} & set(counts)  # all three are in the sample, none is a key


def test_function_words_and_names_are_kept(counts):
    assert counts["的"] >= 8 and counts["了"] >= 8
    assert counts["华盛顿"] >= 3


def test_ranks_are_dense_ordered_and_capped():
    rows = bzf.rank_rows(Counter({"医院": 9, "知道": 9, "什么": 3, "肾上腺素": 1}), cap=3)
    assert rows == [("医院", 1), ("知道", 2), ("什么", 3)]  # ties broken by the term, deterministic


def test_the_written_zip_is_a_rank_based_yomitan_frequency_dictionary(tmp_path):
    out = tmp_path / "opensubtitles-zh-word.zip"
    rows = [(f"词{i}", i + 1) for i in range(12_345)]
    bzf.write_yomitan(rows, out, revision="test", lines=100)
    first = out.read_bytes()
    bzf.write_yomitan(rows, out, revision="test", lines=100)
    assert out.read_bytes() == first  # reproducible bytes, reproducible sha256
    with zipfile.ZipFile(out) as zf:
        assert zf.namelist() == ["index.json", "term_meta_bank_1.json", "term_meta_bank_2.json"]
        index = json.loads(zf.read("index.json"))
        bank = json.loads(zf.read("term_meta_bank_2.json"))
    assert (index["format"], index["frequencyMode"], index["sourceLanguage"]) == (3, "rank-based", "zh")
    assert "ODC-BY" in index["attribution"] and index["revision"] == "test"
    assert bank[0] == ["词10000", "freq", 10_001] and len(bank) == 2_345


def test_the_app_imports_the_asset_and_reads_a_rank(tmp_path, counts):
    from anki_miner.languages.registry import get_profile
    from anki_miner.services.frequency.providers.indexed_freq_provider import IndexedFreqProvider
    from anki_miner.services.frequency.source_importer import import_frequency_source

    out = tmp_path / "opensubtitles-zh-word.zip"
    rows = bzf.rank_rows(counts)
    bzf.write_yomitan(rows, out, revision="test", lines=60)
    result = import_frequency_source(out, tmp_path / "freqs", source_id="opensubtitles-zh-word", language="zh")
    assert result.entry_count == len(rows)
    provider = IndexedFreqProvider(
        "opensubtitles-zh-word",
        tmp_path / "freqs" / "opensubtitles-zh-word" / "index.sqlite",
        "OpenSubtitles 2024 (zh)",
        keys=get_profile("zh").dict_keys,
    )
    assert provider.load()
    rank = provider.lookup("医院")
    assert rank is not None and 1 <= rank <= 40
    assert provider.lookup("醫院") == rank  # a traditional front reaches the row through term_variants
    provider.close()


def test_the_cli_sums_both_gz_corpora_in_process(tmp_path):
    lines = SAMPLE.read_text(encoding="utf-8").splitlines()
    for name, block in (("zh_CN.txt.gz", lines[:35]), ("zh_TW.txt.gz", lines[35:])):
        with gzip.open(tmp_path / name, "wt", encoding="utf-8") as handle:
            handle.write("\n".join(block) + "\n")
    out = tmp_path / "asset.zip"
    argv = [str(tmp_path / "zh_CN.txt.gz"), str(tmp_path / "zh_TW.txt.gz"), str(out)]
    assert bzf.main([*argv, "--revision", "test", "--processes", "1"]) == 0
    with zipfile.ZipFile(out) as zf:
        index = json.loads(zf.read("index.json"))
        bank = json.loads(zf.read("term_meta_bank_1.json"))
    assert index["description"].startswith("60 lines")
    terms = {term for term, _tag, _rank in bank}
    assert {"我们", "这里"} <= terms  # the Taiwan half landed on the simplified keys


def test_a_missing_opencc_stops_the_build(monkeypatch):
    # Without OpenCC to_simplified returns its input: traditional lines are cut by a
    # simplified-only dictionary and no key folds, so the asset must not be built at all.
    monkeypatch.setitem(sys.modules, "opencc", None)
    with pytest.raises(SystemExit, match="OpenCC"):
        bzf.require_opencc()
