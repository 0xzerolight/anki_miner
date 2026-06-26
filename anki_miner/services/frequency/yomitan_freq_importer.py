"""Yomitan-format frequency zip → CSV importer.

Yomitan frequency dictionaries (JPDB, BCCWJ, Innocent Corpus, etc.) ship as a
zip containing ``index.json`` plus one or more ``term_meta_bank_*.json`` files.
Each meta-bank is a flat JSON array of ``[term, mode, data]`` triples where
``mode`` is one of ``"freq"``, ``"pitch"``, or ``"ipa"``; this importer extracts
only ``mode == "freq"`` rows, normalizes the five spec-defined ``data`` shapes
to ``(term, int_rank)`` pairs, and writes the result to a ``frequency.csv`` that
the Task-4 startup migration folds into the additive frequency chain.

Per the v1 design decisions:
- Single-source: the output CSV overwrites the configured frequency path.
- Term-only key: reading is intentionally discarded; on collisions, ``min(rank)``
  wins so the user always sees the most-favourable rank for a homograph.
- Rank-based only: ``frequencyMode == "occurrence-based"`` dicts are rejected
  rather than silently re-ranked.

Shared zip extraction, index validation, the strict ``format == 3`` gate, the
per-file progress/cancel loop, and the atomic CSV write live in
:mod:`anki_miner.services.yomitan_meta_bank`; rank normalization lives in
:mod:`anki_miner.services.frequency.csv_parse`. Only the frequency-specific
``frequencyMode`` check remains here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from anki_miner.exceptions import SetupError
from anki_miner.services.frequency.csv_parse import normalize_freq_rank as _normalize_freq_rank
from anki_miner.services.yomitan_meta_bank import (
    ProgressFn,
    atomic_write_csv,
    open_yomitan_meta_banks,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class YomitanFreqImportResult:
    """Outcome of a successful Yomitan frequency import."""

    source_name: str
    source_revision: str
    entry_count: int
    skipped_display_only: int


def import_yomitan_freq_zip(
    zip_path: Path,
    dest_csv: Path,
    *,
    progress: ProgressFn | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> YomitanFreqImportResult:
    """Import a Yomitan frequency zip into ``dest_csv``.

    Args:
        zip_path: Path to the Yomitan-format frequency zip.
        dest_csv: Output CSV path. Written atomically.
        progress: Optional ``(current, total, message)`` callback fired per
            ``term_meta_bank_*.json`` file processed.
        cancel_check: Optional zero-arg predicate; if it returns True between
            files, the import aborts and the existing ``dest_csv`` is left
            untouched.

    Raises:
        SetupError: On invalid input, missing meta banks, occurrence-based mode,
            corrupt JSON, or unsafe zip paths.
    """
    with open_yomitan_meta_banks(zip_path, kind="frequency") as banks:
        if banks.index.frequency_mode == "occurrence-based":
            raise SetupError(
                f"'{banks.title}' is an occurrence-based frequency dictionary. "
                "anki_miner only supports rank-based dictionaries (e.g. JPDB, BCCWJ). "
                "Use a rank-based dict, or convert this one externally before importing."
            )

        ranks: dict[str, int] = {}
        skipped_display_only = 0

        for bank in banks.iter_banks(progress=progress, cancel_check=cancel_check):
            for entry in bank:
                if not isinstance(entry, list) or len(entry) < 3:
                    continue
                if entry[1] != "freq":
                    continue
                term = str(entry[0]).strip() if entry[0] is not None else ""
                if not term:
                    continue

                rank = _normalize_freq_rank(entry[2])
                if rank is None:
                    skipped_display_only += 1
                    continue

                existing = ranks.get(term)
                if existing is None or rank < existing:
                    ranks[term] = rank

        title = banks.title
        revision = banks.revision

        if not ranks:
            raise SetupError(
                f"'{title}' yielded no usable frequency entries (skipped "
                f"{skipped_display_only} display-only entries). "
                "The dictionary may use an unsupported data format."
            )

        # term,rank — sorted by rank for stable, human-scannable output.
        rows = [(term, rank) for term, rank in sorted(ranks.items(), key=lambda kv: kv[1])]
        atomic_write_csv(dest_csv, ["term", "rank"], rows)

    logger.info(
        "Imported %d frequency entries from '%s' (revision '%s'), skipped %d display-only",
        len(ranks),
        title,
        revision,
        skipped_display_only,
    )

    return YomitanFreqImportResult(
        source_name=title,
        source_revision=revision,
        entry_count=len(ranks),
        skipped_display_only=skipped_display_only,
    )
