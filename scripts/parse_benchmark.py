#!/usr/bin/env python
"""Parsing benchmark harness — score word-parsing strategies against the corpus.

Dev/CI tool. NEVER imported by the app (it lives in ``scripts/``, outside the
``anki_miner`` package, and ``tests/unit/test_parse_benchmark.py`` guards that).

What it does
------------
Loads the hand-labeled corpus under ``tests/fixtures/parse_corpus/`` (one JSONL
record per line: ``{"id", "sentence", "expected", "category", "note"?}``), runs
each registered strategy over every sentence, and reports precision / recall /
junk-rate / miss-rate / F1 per strategy x category and overall — as a stdout
table and, with ``--csv``, a CSV file.

A **strategy** is a plain function ``mine(sentence: str) -> set[str]`` returning
the set of mined ``TokenizedWord.mined_form`` card fronts. Strategies live in the
``STRATEGIES`` dict (no Protocol/registry class — YAGNI; Task 3 adds a second
strategy as another function). Today there is exactly one:

* ``a-lite-orthbase`` = the app's behavior on ``main`` today. It drives the REAL
  ``SubtitleParserService.parse_text_units`` so the harness can never diverge
  from what real mining emits (a known bug class in this repo). It reuses the
  service's own tokenize -> merge -> inclusion -> emit path; it does NOT
  re-implement ``_emit_word`` / ``mining_base``. No optional coverage filters
  (i+1 / frequency / word-list / dedup / sentence-length) run on this path —
  those live downstream in the orchestrator, not in the parser — so the output
  is the raw pre-filter mined set, exactly the raw-lemma view the Deck Builder
  gets with ``bypass_optional_filters=True``.

Aggregation is **micro-averaged**: within a category (and overall) we sum the
per-record set-intersection counts and divide once, rather than averaging
per-sentence ratios. Micro-averaging weights every mined/expected token equally
regardless of which sentence it came from, which is the meaningful quantity for
a "how many cards are right / junk / missing" scoreboard.

Usage
-----
    python scripts/parse_benchmark.py [--csv PATH] [--corpus DIR] [--category CAT]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

# The harness reaches into the app package for the REAL parser (strategy a).
# scripts/ is not on sys.path when run as ``python scripts/parse_benchmark.py``,
# so add the repo root before importing anki_miner.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from anki_miner.config.config import AnkiMinerConfig  # noqa: E402
from anki_miner.models.reading import ReadingUnit  # noqa: E402
from anki_miner.services.subtitle_parser import SubtitleParserService  # noqa: E402

Strategy = Callable[[str], set[str]]

DEFAULT_CORPUS_DIR = _REPO_ROOT / "tests" / "fixtures" / "parse_corpus"

_REQUIRED_KEYS = ("id", "sentence", "expected", "category")


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


@dataclass
class Counts:
    """Micro-average accumulator: summed intersection / mined / expected sizes.

    ``tp`` is the running sum of ``|mined ∩ expected|``, ``mined_total`` of
    ``|mined|`` and ``expected_total`` of ``|expected|`` across every record
    folded in. The five metric functions read a ``Counts`` so a per-record
    ``Counts.of(...)`` and a whole-category aggregate use the identical math.
    """

    tp: int = 0
    mined_total: int = 0
    expected_total: int = 0

    def add(self, mined: set[str], expected: set[str]) -> None:
        """Fold one record's mined/expected sets into the running totals."""
        self.tp += len(mined & expected)
        self.mined_total += len(mined)
        self.expected_total += len(expected)

    @classmethod
    def of(cls, mined: set[str], expected: set[str]) -> Counts:
        """Build a single-record ``Counts`` (convenience for callers/tests)."""
        c = cls()
        c.add(mined, expected)
        return c


def precision(c: Counts) -> float:
    """|mined ∩ expected| / |mined|.

    1.0 when nothing was mined: with ``|mined|==0`` and ``|expected|==0`` the
    parse is trivially correct, and with ``|mined|==0`` and ``|expected|>0``
    precision is undefined — treated as 1.0 by convention while ``recall``
    carries the miss.
    """
    return 1.0 if c.mined_total == 0 else c.tp / c.mined_total


def recall(c: Counts) -> float:
    """|mined ∩ expected| / |expected|; 1.0 when nothing was expected."""
    return 1.0 if c.expected_total == 0 else c.tp / c.expected_total


def junk_rate(c: Counts) -> float:
    """|mined − expected| / |mined|; 0.0 when nothing was mined."""
    return 0.0 if c.mined_total == 0 else (c.mined_total - c.tp) / c.mined_total


def miss_rate(c: Counts) -> float:
    """|expected − mined| / |expected|; 0.0 when nothing was expected."""
    return 0.0 if c.expected_total == 0 else (c.expected_total - c.tp) / c.expected_total


def f1(c: Counts) -> float:
    """Harmonic mean of precision and recall; 0.0 when both are 0."""
    p = precision(c)
    r = recall(c)
    return 0.0 if (p + r) == 0 else 2 * p * r / (p + r)


_METRIC_FUNCS: tuple[tuple[str, Callable[[Counts], float]], ...] = (
    ("precision", precision),
    ("recall", recall),
    ("junk_rate", junk_rate),
    ("miss_rate", miss_rate),
    ("f1", f1),
)


def metrics_for(c: Counts) -> dict[str, float]:
    """Return all five metrics for a ``Counts`` as a name→value dict."""
    return {name: fn(c) for name, fn in _METRIC_FUNCS}


# ---------------------------------------------------------------------------
# Corpus loading
# ---------------------------------------------------------------------------


def load_corpus(corpus_dir: Path) -> list[dict]:
    """Load every ``*.jsonl`` record under ``corpus_dir`` into a flat list.

    Each line is one JSON object. Blank lines are skipped. A record missing a
    required key (``id`` / ``sentence`` / ``expected`` / ``category``) or with an
    empty ``category`` raises ``ValueError`` — the corpus is curated ground truth
    and a malformed row is a bug, not something to silently drop.

    Raises:
        FileNotFoundError / NotADirectoryError: ``corpus_dir`` is not a directory.
    """
    if not corpus_dir.is_dir():
        raise FileNotFoundError(f"Corpus directory not found: {corpus_dir}")

    records: list[dict] = []
    for path in sorted(corpus_dir.glob("*.jsonl")):
        with path.open(encoding="utf-8") as fh:
            for line_no, raw in enumerate(fh, start=1):
                line = raw.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError as e:
                    raise ValueError(f"{path.name}:{line_no}: invalid JSON: {e}") from e
                for key in _REQUIRED_KEYS:
                    if key not in rec:
                        raise ValueError(f"{path.name}:{line_no}: record missing {key!r}")
                if not isinstance(rec["expected"], list):
                    raise ValueError(f"{path.name}:{line_no}: 'expected' must be a list")
                if not str(rec["category"]).strip():
                    raise ValueError(f"{path.name}:{line_no}: empty 'category'")
                records.append(rec)
    return records


# ---------------------------------------------------------------------------
# Strategy (a) — the REAL app behavior on main today
# ---------------------------------------------------------------------------

_service: SubtitleParserService | None = None


def _get_service() -> SubtitleParserService:
    """Lazily build one shared ``SubtitleParserService`` on a default config.

    Built without a ``term_lookup``/``reading_lookup`` so the benchmark is
    deterministic, network-free and independent of whichever offline
    dictionaries a machine happens to have imported — the compound matcher only
    affects multi-token dictionary-attested spans, which the meaningful corpus
    categories never touch. ``parse_text_units`` resets its per-parse caches on
    every call, so reusing one instance across sentences is safe.
    """
    global _service
    if _service is None:
        _service = SubtitleParserService(AnkiMinerConfig())
    return _service


def mine_lite_orthbase(sentence: str) -> set[str]:
    """Strategy (a): the mined-form set the real app produces today.

    Drives ``SubtitleParserService.parse_text_units`` — the real tokenize ->
    merge -> inclusion -> emit pipeline (same ``_emit_word``/``mining_base`` the
    app uses) over a single ``ReadingUnit`` — and returns the emitted
    ``mined_form`` set. No optional coverage filters run on this path.
    """
    service = _get_service()
    unit = ReadingUnit(text=sentence, index=0, location_label="benchmark")
    words, _index, _counts = service.parse_text_units([unit], want_line_index=False)
    return {w.mined_form for w in words}


STRATEGIES: dict[str, Strategy] = {
    "a-lite-orthbase": mine_lite_orthbase,
}


# ---------------------------------------------------------------------------
# Benchmark run
# ---------------------------------------------------------------------------


@dataclass
class StrategyResult:
    """Scored output of one strategy over the corpus."""

    name: str
    by_category: dict[str, Counts] = field(default_factory=dict)
    overall: Counts = field(default_factory=Counts)


def run_benchmark(records: Iterable[dict], strategies: dict[str, Strategy]) -> dict[str, StrategyResult]:
    """Run every strategy over every record, accumulating micro-average counts.

    Returns a ``{strategy_name: StrategyResult}`` map. Each ``StrategyResult``
    holds per-category ``Counts`` plus an overall ``Counts`` spanning all
    records.
    """
    records = list(records)
    results: dict[str, StrategyResult] = {}
    for name, fn in strategies.items():
        by_category: dict[str, Counts] = defaultdict(Counts)
        overall = Counts()
        for rec in records:
            mined = fn(rec["sentence"])
            expected = set(rec["expected"])
            by_category[rec["category"]].add(mined, expected)
            overall.add(mined, expected)
        results[name] = StrategyResult(name=name, by_category=dict(by_category), overall=overall)
    return results


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

_TABLE_COLUMNS = ("precision", "recall", "junk_rate", "miss_rate", "f1")


def format_table(results: dict[str, StrategyResult]) -> str:
    """Render the strategy x category x metrics scoreboard as a text table."""
    lines: list[str] = []
    header = f"{'strategy':<18} {'category':<18} " + " ".join(f"{col:>10}" for col in _TABLE_COLUMNS)
    for name, result in results.items():
        lines.append("")
        lines.append(header)
        lines.append("-" * len(header))
        for category in sorted(result.by_category):
            lines.append(_format_row(name, category, result.by_category[category]))
        lines.append(_format_row(name, "OVERALL", result.overall))
    return "\n".join(lines).strip("\n")


def _format_row(strategy: str, category: str, counts: Counts) -> str:
    metrics = metrics_for(counts)
    cells = " ".join(f"{metrics[col]:>10.3f}" for col in _TABLE_COLUMNS)
    return f"{strategy:<18} {category:<18} {cells}"


def write_csv(path: Path, results: dict[str, StrategyResult]) -> None:
    """Write the per strategy x category (+ OVERALL) metrics to a CSV file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = ["strategy", "category", "tp", "mined_total", "expected_total", *_TABLE_COLUMNS]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for name, result in results.items():
            for category in sorted(result.by_category):
                writer.writerow(_csv_row(name, category, result.by_category[category]))
            writer.writerow(_csv_row(name, "OVERALL", result.overall))


def _csv_row(strategy: str, category: str, counts: Counts) -> dict[str, object]:
    metrics = metrics_for(counts)
    row: dict[str, object] = {
        "strategy": strategy,
        "category": category,
        "tp": counts.tp,
        "mined_total": counts.mined_total,
        "expected_total": counts.expected_total,
    }
    for col in _TABLE_COLUMNS:
        row[col] = f"{metrics[col]:.6f}"
    return row


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code (0 = success)."""
    parser = argparse.ArgumentParser(description="Score word-parsing strategies against the labeled corpus.")
    parser.add_argument("--csv", type=Path, default=None, help="Also write per-category metrics to this CSV path.")
    parser.add_argument(
        "--corpus",
        type=Path,
        default=DEFAULT_CORPUS_DIR,
        help=f"Corpus directory of *.jsonl fixtures (default: {DEFAULT_CORPUS_DIR}).",
    )
    parser.add_argument("--category", default=None, help="Restrict scoring to a single category.")
    args = parser.parse_args(argv)

    records = load_corpus(args.corpus)
    if args.category is not None:
        records = [r for r in records if r["category"] == args.category]
        if not records:
            print(f"No records for category {args.category!r} in {args.corpus}", file=sys.stderr)
            return 1

    results = run_benchmark(records, STRATEGIES)
    print(format_table(results))
    if args.csv is not None:
        write_csv(args.csv, results)
        print(f"\nWrote CSV: {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
