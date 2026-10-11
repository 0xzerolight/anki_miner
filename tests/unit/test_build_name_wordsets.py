"""Unit tests for scripts/build_name_wordsets.py — bucketing, drop rules, header, report."""

import json
import sys
import zipfile
from pathlib import Path

import pytest

# scripts/ is not a package; insert repo root so the module is importable.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.build_name_wordsets import build_wordsets  # noqa: E402

FIXTURES = Path(__file__).parent.parent / "fixtures"
BUCKETS = ("surnames", "given-names", "place-names", "org-product")

# Jiten-shaped rows: a plain int, a reading envelope, and a ㋕ kana-usage row whose
# rank must be ignored (it would put 鬼灯 inside the cutoff).
_RANKS = [
    ["魔王", "freq", 7820],
    ["鬼灯", "freq", {"reading": "ほおずき", "frequency": {"value": 25000, "displayValue": "25000"}}],
    ["鬼灯", "freq", {"value": 100, "displayValue": "100㋕"}],
    ["凛", "freq", 900],
]


def _freq_zip(tmp_path: Path, rows: list, mode: str = "rank-based") -> Path:
    path = tmp_path / "jiten.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("index.json", json.dumps({"title": "Jiten", "format": 3, "frequencyMode": mode}))
        archive.writestr("term_meta_bank_1.json", json.dumps(rows, ensure_ascii=False))
    return path


def _build(tmp_path: Path, report: Path | None = None) -> dict[str, str]:
    out = tmp_path / "out"
    build_wordsets(
        jmnedict=FIXTURES / "jmnedict_mini.xml",
        jmdict=FIXTURES / "jmdict_mini.xml",
        freq=_freq_zip(tmp_path, _RANKS),
        out_dir=out,
        report=report,
    )
    return {bucket: (out / f"{bucket}.txt").read_text(encoding="utf-8") for bucket in BUCKETS}


def _entries(text: str) -> set[str]:
    return {line for line in text.splitlines() if line and not line.startswith("#")}


def test_buckets_and_drops_common_overlaps(tmp_path):
    files = _build(tmp_path)
    assert "田中" in _entries(files["surnames"])
    assert "渋谷" in _entries(files["place-names"])
    assert "太郎" in _entries(files["given-names"])
    assert "ソニー" in _entries(files["org-product"])
    assert "春" not in _entries(files["given-names"])  # common JMdict word → dropped
    assert "# id: surnames" in files["surnames"]
    assert "# label: Surnames" in files["surnames"]
    assert "# count:" in files["surnames"]
    assert "# filter:" in files["surnames"]


def test_everyday_word_in_the_top_ranks_is_dropped(tmp_path):
    assert "魔王" not in _entries(_build(tmp_path)["given-names"])


def test_word_ranked_past_the_cutoff_stays_a_name(tmp_path):
    # Its ㋕ row ranks 100; only the kanji-usage rank (25000) counts.
    assert "鬼灯" in _entries(_build(tmp_path)["place-names"])


def test_single_character_word_stays_a_name(tmp_path):
    assert "凛" in _entries(_build(tmp_path)["given-names"])


def test_word_plus_particle_is_dropped(tmp_path):
    assert "夢の" not in _entries(_build(tmp_path)["place-names"])


def test_names_ending_in_particle_kana_are_kept(tmp_path):
    given = _entries(_build(tmp_path)["given-names"])
    assert "亜矢の" in given  # 亜矢 is no JMdict word
    assert "小さと" in given  # 小さ is a JMdict word, but not all kanji


def test_report_lists_each_drop_with_its_reason(tmp_path):
    report = tmp_path / "dropped.tsv"
    _build(tmp_path, report=report)
    lines = set(report.read_text(encoding="utf-8").splitlines())
    assert "魔王\teveryday\t7820" in lines
    assert "夢の\tparticle\t" in lines


def test_occurrence_based_frequency_list_is_refused(tmp_path):
    with pytest.raises(ValueError, match="rank-based"):
        build_wordsets(
            jmnedict=FIXTURES / "jmnedict_mini.xml",
            jmdict=FIXTURES / "jmdict_mini.xml",
            freq=_freq_zip(tmp_path, _RANKS, mode="occurrence-based"),
            out_dir=tmp_path / "out",
        )
