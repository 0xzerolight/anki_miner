"""Tests for the parsing benchmark harness (scripts/parse_benchmark.py).

The harness is a dev/CI tool that scores word-parsing strategies against the
hand-labeled corpus under ``tests/fixtures/parse_corpus/``. It is NOT part of
the app import surface (it lives in ``scripts/`` outside the ``anki_miner``
package); the ``test_harness_not_imported_by_app`` guard enforces that.

Coverage split:

* Metric math — exercised on synthetic mined/expected sets (no tagger).
* Harness plumbing (loader, run_benchmark, table, CSV) — exercised with a fake
  strategy so it stays fast and tagger-independent.
* Strategy (a) smoke — drives the REAL ``SubtitleParserService`` on a handful of
  sentences and only asserts shape/liveness, never exact linguistic values
  (pinning today's output would just re-encode the bug the overhaul fixes).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# scripts/ is not a package; insert the repo root so the module is importable.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.parse_benchmark import (  # noqa: E402
    DEFAULT_CORPUS_DIR,
    STRATEGIES,
    Counts,
    f1,
    format_table,
    junk_rate,
    load_corpus,
    main,
    miss_rate,
    precision,
    recall,
    run_benchmark,
    write_csv,
)

# ---------------------------------------------------------------------------
# Metric math (synthetic sets, no tagger)
# ---------------------------------------------------------------------------


def _m(mined: set[str], expected: set[str]) -> dict[str, float]:
    c = Counts.of(mined, expected)
    return {
        "precision": precision(c),
        "recall": recall(c),
        "junk_rate": junk_rate(c),
        "miss_rate": miss_rate(c),
        "f1": f1(c),
    }


def test_metrics_perfect_match() -> None:
    r = _m({"a", "b"}, {"a", "b"})
    assert r == {"precision": 1.0, "recall": 1.0, "junk_rate": 0.0, "miss_rate": 0.0, "f1": 1.0}


def test_metrics_both_empty() -> None:
    # No card mined, none expected: precision/recall are 1.0 by convention,
    # junk/miss are 0.0, F1 is 1.0.
    r = _m(set(), set())
    assert r == {"precision": 1.0, "recall": 1.0, "junk_rate": 0.0, "miss_rate": 0.0, "f1": 1.0}


def test_metrics_mined_empty_expected_nonempty() -> None:
    # A pure miss: nothing mined but something expected. Precision is undefined
    # (|mined|==0) → 1.0 by convention, but recall carries the miss (0.0) and F1
    # collapses to 0.0.
    r = _m(set(), {"a"})
    assert r["precision"] == 1.0
    assert r["recall"] == 0.0
    assert r["junk_rate"] == 0.0
    assert r["miss_rate"] == 1.0
    assert r["f1"] == 0.0


def test_metrics_mined_nonempty_expected_empty() -> None:
    # Pure junk: a card mined where none was wanted.
    r = _m({"a"}, set())
    assert r["precision"] == 0.0
    assert r["recall"] == 1.0
    assert r["junk_rate"] == 1.0
    assert r["miss_rate"] == 0.0
    assert r["f1"] == 0.0


def test_metrics_partial_overlap() -> None:
    r = _m({"a", "b"}, {"a", "c"})
    assert r["precision"] == 0.5
    assert r["recall"] == 0.5
    assert r["junk_rate"] == 0.5
    assert r["miss_rate"] == 0.5
    assert r["f1"] == 0.5


def test_metrics_micro_average_across_records() -> None:
    # Aggregation is micro (sum intersection counts), NOT a mean of per-record
    # ratios. Record 1: mined {a,b}, expected {a} → tp 1, mined 2, exp 1.
    # Record 2: mined {c}, expected {c,d} → tp 1, mined 1, exp 2.
    # Totals: tp 2, mined 3, exp 3 → precision 2/3, recall 2/3.
    c = Counts()
    c.add({"a", "b"}, {"a"})
    c.add({"c"}, {"c", "d"})
    assert c.tp == 2
    assert c.mined_total == 3
    assert c.expected_total == 3
    assert precision(c) == pytest.approx(2 / 3)
    assert recall(c) == pytest.approx(2 / 3)
    assert junk_rate(c) == pytest.approx(1 / 3)
    assert miss_rate(c) == pytest.approx(1 / 3)
    assert f1(c) == pytest.approx(2 / 3)


def test_counts_of_is_single_record_helper() -> None:
    c = Counts.of({"a", "b"}, {"b", "c"})
    assert (c.tp, c.mined_total, c.expected_total) == (1, 2, 2)


# ---------------------------------------------------------------------------
# Corpus loader
# ---------------------------------------------------------------------------

_EXPECTED_CATEGORIES = {
    "jiru-zuru",
    "archaic-lemma",
    "kanji-variant",
    "kana-written",
    "potential-ranuki",
    "cross-conjugation",
    "katakana",
    "nominal-suffix",
    "long-compound",
    "aux-context",
    "aux-keijoushi",
    "colloquial",
    "counter",
    "linebreak-split",
}


def test_load_corpus_parses_every_fixture() -> None:
    records = load_corpus(DEFAULT_CORPUS_DIR)
    # 14 fixture files, 54 curated records at authoring time; never fewer.
    assert len(records) >= 50
    categories = set()
    ids = set()
    for rec in records:
        # Required keys present.
        for key in ("id", "sentence", "expected", "category"):
            assert key in rec, f"record missing {key!r}: {rec}"
        assert isinstance(rec["id"], str) and rec["id"], rec
        assert isinstance(rec["sentence"], str) and rec["sentence"], rec
        assert isinstance(rec["expected"], list), rec
        assert all(isinstance(w, str) for w in rec["expected"]), rec
        # Non-empty category.
        assert isinstance(rec["category"], str) and rec["category"].strip(), rec
        categories.add(rec["category"])
        assert rec["id"] not in ids, f"duplicate id {rec['id']!r}"
        ids.add(rec["id"])
    # Every documented category is represented.
    assert categories == _EXPECTED_CATEGORIES


def test_load_corpus_missing_dir_raises(tmp_path: Path) -> None:
    with pytest.raises((FileNotFoundError, NotADirectoryError)):
        load_corpus(tmp_path / "does-not-exist")


# ---------------------------------------------------------------------------
# Harness plumbing (fake strategy — no tagger)
# ---------------------------------------------------------------------------


def _fake_records() -> list[dict]:
    return [
        {"id": "x1", "sentence": "s1", "expected": ["a"], "category": "cat1"},
        {"id": "x2", "sentence": "s2", "expected": ["b", "c"], "category": "cat1"},
        {"id": "x3", "sentence": "s3", "expected": [], "category": "cat2"},
    ]


def _fake_strategy(sentence: str) -> set[str]:
    # Deterministic, tagger-free mapping for plumbing tests.
    return {
        "s1": {"a"},  # perfect hit
        "s2": {"b", "z"},  # one hit, one junk
        "s3": set(),  # correctly mines nothing
    }[sentence]


def test_run_benchmark_aggregates_by_category_and_overall() -> None:
    results = run_benchmark(_fake_records(), {"fake": _fake_strategy})
    res = results["fake"]
    cat1 = res.by_category["cat1"]
    # s1: tp1/mined1/exp1 ; s2: tp1/mined2/exp2 → cat1 tp2, mined3, exp3.
    assert (cat1.tp, cat1.mined_total, cat1.expected_total) == (2, 3, 3)
    cat2 = res.by_category["cat2"]
    assert (cat2.tp, cat2.mined_total, cat2.expected_total) == (0, 0, 0)
    # Overall spans every record.
    assert (res.overall.tp, res.overall.mined_total, res.overall.expected_total) == (2, 3, 3)


def test_format_table_mentions_strategy_and_categories() -> None:
    results = run_benchmark(_fake_records(), {"fake": _fake_strategy})
    table = format_table(results)
    assert "fake" in table
    assert "cat1" in table
    assert "cat2" in table
    assert "overall" in table.lower()


def test_write_csv_roundtrip(tmp_path: Path) -> None:
    import csv

    results = run_benchmark(_fake_records(), {"fake": _fake_strategy})
    out = tmp_path / "bench.csv"
    write_csv(out, results)
    assert out.exists()
    with out.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert rows, "CSV wrote no data rows"
    header = set(rows[0].keys())
    for col in ("strategy", "category", "precision", "recall", "junk_rate", "miss_rate", "f1"):
        assert col in header, f"missing CSV column {col!r}"
    # The fake strategy over cat1 (micro): precision 2/3.
    cat1_row = next(r for r in rows if r["strategy"] == "fake" and r["category"] == "cat1")
    assert float(cat1_row["precision"]) == pytest.approx(2 / 3)


# ---------------------------------------------------------------------------
# Strategy (a) smoke — drives the REAL SubtitleParserService
# ---------------------------------------------------------------------------


def test_strategy_a_is_registered() -> None:
    # Exactly one strategy today; Task 3 adds a second.
    assert len(STRATEGIES) >= 1
    fn = next(iter(STRATEGIES.values()))
    assert callable(fn)


def test_strategy_a_returns_str_set_and_is_live() -> None:
    fn = next(iter(STRATEGIES.values()))
    union: set[str] = set()
    for sentence in ("立った", "待った", "言った"):
        mined = fn(sentence)
        assert isinstance(mined, set)
        assert all(isinstance(w, str) for w in mined)
        union |= mined
    # Liveness only: the real pipeline mined *something* from content sentences.
    # No exact-value assertion — the corpus scoring owns app-behavior checks.
    assert union, "strategy (a) mined nothing from content sentences"


def test_main_runs_end_to_end_on_category_subset(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "cc.csv"
    rc = main(["--category", "cross-conjugation", "--csv", str(out)])
    assert rc == 0
    assert out.exists()
    printed = capsys.readouterr().out
    assert "cross-conjugation" in printed


# ---------------------------------------------------------------------------
# Import-surface guard: the app must never import the dev harness.
# ---------------------------------------------------------------------------


def test_harness_not_imported_by_app() -> None:
    pkg_root = _REPO_ROOT / "anki_miner"
    offenders = []
    for path in pkg_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "parse_benchmark" in text:
            offenders.append(str(path.relative_to(_REPO_ROOT)))
    assert not offenders, f"app modules reference the dev harness: {offenders}"
