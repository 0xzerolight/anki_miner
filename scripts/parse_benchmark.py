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

import tempfile  # noqa: E402
from dataclasses import replace  # noqa: E402

from anki_miner.config.config import AnkiMinerConfig, ChainEntry  # noqa: E402
from anki_miner.models.reading import ReadingUnit  # noqa: E402
from anki_miner.services.definition_service import DefinitionService  # noqa: E402
from anki_miner.services.dictionary.registry import DictionaryRegistry  # noqa: E402
from anki_miner.services.dictionary.storage import (  # noqa: E402
    SCHEMA_VERSION,
    DictRow,
    bulk_insert,
    create_index,
    write_meta,
)
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


# ---------------------------------------------------------------------------
# Strategy (b) — the REAL app pipeline WITH the resolver active
# ---------------------------------------------------------------------------

# Headwords the deterministic fixture dictionary attests. Both consumers gate on
# EXISTENCE — the resolver (``resolve_dictionary_form``) and the WS2 kana
# recovery (``has_offline_definitions``) never read glosses or scores — so a bare
# ``term`` per line is all either fix needs. The set covers every corpus target a
# 動詞/形容詞 resolver, compound-existence probe, or kana-recovery probe can key
# on: the seven modern じる headwords the resolver must recover, plus the guard
# forms whose orthBase is ALREADY the correct headword (乞う, 立つ, 見る, …) so a
# strictly-greater override can never fire, plus the kana/kanji adjective pairs
# and kana 形状詞/形容詞 the recovery attests (きれい, すごい, かわいい, あざとい,
# しがない) and the nominal-suffix / prefix compound headwords the attested-or-bail
# merge gate must keep whole (重要性, 刑務所, 不可能) for a fair (b) table — while
# 状況的/会議中/超反応 are deliberately absent so the gate bails them to bare nouns.
# The kana forms are stored as ``term`` (not ``reading``), so the term-OR-reading probe finds
# them by term and ``reading`` stays empty — production's real JMdict attests the
# same kana as a READING, which the same probe also matches. This list is the
# single source of truth for the fixture index and is committed here — the index
# itself is rebuilt from it at benchmark start (no binary blob in git, fully
# reproducible, network-free).
_ANCHOR_HEADWORDS: tuple[str, ...] = (
    # jiru-zuru modern headwords (the fix's targets)
    "感じる",
    "論じる",
    "信じる",
    "生じる",
    "演じる",
    "通じる",
    "準じる",
    # archaic-lemma / kanji-variant / cross-conjugation / potential guards
    "乞う",
    "彷徨う",
    "出逢う",
    "報いる",
    "帰れる",
    "見る",
    "保つ",
    "立つ",
    "待つ",
    "言う",
    # katakana loanword verb
    "サボる",
    # kana / kanji adjective pairs + non-priority adjectives
    "きれい",
    "綺麗",
    "すごい",
    "凄い",
    "かわいい",
    "可愛い",
    "あざとい",
    "しがない",
    # nominal-suffix / prefix compound headwords the gate must keep whole
    # (刑務所/不可能 are attested; 状況的/会議中/超反応 are deliberately ABSENT so
    # the attested-or-bail gate bails them to their bare nouns).
    "重要性",
    "刑務所",
    "不可能",
    # colloquial orthBase targets (kana recovery mines these from すげえ/やべえ/
    # うめえ/わかんない; the expected form is the orthBase, never the kanji lemma)
    "やばい",
    "うまい",
    "わかる",
    # auxiliary verbs, DELIBERATELY attested: the aux-context category must fail
    # on attestation-PASS + pos2-reject, not on a fixture-dict miss — otherwise
    # the floor stays green even if the 非自立可能 reject is reverted (the same
    # false-safe the wired-lookup unit tests exist to prevent). する is NOT
    # anchored (しちゃった must stay unminted via miss either way).
    "いる",
    "ある",
    "くれる",
    "おく",
    "しまう",
    # attested collocation for the long-compound swallow-by-design fixture
    "気がする",
    # long-compound (Task 6): the 2-token attested compounds the matcher must
    # keep whole — 応急処置 (attestation-only) and the 13-char katakana compound
    # the 16-char span cap exists for. The 14-char greeting is DELIBERATELY
    # attested: it must still NOT merge (7 tokens > the 5-token cap) — the
    # adversarial proof the token cap holds even for attested long phrases.
    "応急処置",
    "アプリケーションプログラム",
    "お誕生日おめでとうございます",
)

_ANCHOR_DICT_ID = "anchor-fixture"
_anchor_service: SubtitleParserService | None = None


def build_anchor_index(dicts_root: Path, dict_id: str = _ANCHOR_DICT_ID) -> Path:
    """Seed a deterministic offline index of ``_ANCHOR_HEADWORDS`` under ``dicts_root``.

    Reuses the production storage primitives (``create_index`` / ``bulk_insert``
    / ``write_meta`` — the exact path a real Yomitan/JMdict import writes), so
    the fixture can never diverge from a real index's schema. Returns the
    ``index.sqlite`` path. Pure disk write under the caller-owned ``dicts_root``;
    no network, no ``~/.anki_miner``.
    """
    folder = dicts_root / dict_id
    folder.mkdir(parents=True, exist_ok=True)
    db = folder / "index.sqlite"
    create_index(db)
    rows = [
        DictRow(term=term, reading=None, content=f'<li class="gloss-item">{term}</li>', sequence=i)
        for i, term in enumerate(_ANCHOR_HEADWORDS, start=1)
    ]
    bulk_insert(db, rows)
    write_meta(
        db,
        {
            "schema_version": str(SCHEMA_VERSION),
            "source_name": dict_id,
            "format": "yomitan",
            "entry_count": str(len(rows)),
        },
    )
    return db


def _get_anchor_service() -> SubtitleParserService:
    """Lazily build one ``SubtitleParserService`` wired to the fixture index.

    Builds the deterministic index under a fresh temp dir (isolated from the
    user's ``~/.anki_miner``), assembles the real provider chain via
    ``DictionaryRegistry`` + ``DefinitionService``, and injects the SAME probes
    production wires: ``offline_terms_exist`` as ``term_lookup`` (so
    ``resolve_dictionary_form`` fires) and ``has_offline_definitions`` as
    ``kana_attest_lookup`` (so the WS2 pure-hiragana kana recovery fires). Same
    real parse path as strategy (a); the ONLY difference is the live probes.
    """
    global _anchor_service
    if _anchor_service is None:
        root = Path(tempfile.mkdtemp(prefix="parse_benchmark_anchor_"))
        build_anchor_index(root)
        config = replace(
            AnkiMinerConfig(),
            dicts_root=root,
            dictionary_chain=(ChainEntry(kind="indexed", dict_id=_ANCHOR_DICT_ID, enabled=True),),
            media_temp_folder=root / "media",
        )
        registry = DictionaryRegistry(config.dicts_root)
        registry.load()
        definition_service = DefinitionService(config, providers=registry.build_provider_chain(config))
        _anchor_service = SubtitleParserService(
            config,
            term_lookup=definition_service.offline_terms_exist,
            kana_attest_lookup=definition_service.has_offline_definitions,
        )
    return _anchor_service


def mine_lite_anchor(sentence: str) -> set[str]:
    """Strategy (b): the real pipeline WITH the JMdict-anchored dict active.

    Identical to ``mine_lite_orthbase`` — same real
    ``SubtitleParserService.parse_text_units`` path, same ``_emit_word`` /
    ``mining_base`` — except the service carries offline probes backed by the
    fixture index: the ``term_lookup`` rewrites archaic じる/ずる orthBases
    (感ずる → 感じる), and the ``kana_attest_lookup`` recovers pure-hiragana
    content words the script gate drops (きれい, すごい, かわいい) when the
    fixture attests them (WS2).
    """
    service = _get_anchor_service()
    unit = ReadingUnit(text=sentence, index=0, location_label="benchmark")
    words, _index, _counts = service.parse_text_units([unit], want_line_index=False)
    return {w.mined_form for w in words}


STRATEGIES: dict[str, Strategy] = {
    "a-lite-orthbase": mine_lite_orthbase,
    "b-lite-anchor": mine_lite_anchor,
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
