"""Yomitan-format frequency zip → CSV importer.

Yomitan frequency dictionaries (JPDB, BCCWJ, Innocent Corpus, etc.) ship as a
zip containing ``index.json`` plus one or more ``term_meta_bank_*.json`` files.
Each meta-bank is a flat JSON array of ``[term, mode, data]`` triples where
``mode`` is one of ``"freq"``, ``"pitch"``, or ``"ipa"``; this importer extracts
only ``mode == "freq"`` rows, normalizes the five spec-defined ``data`` shapes
to ``(term, int_rank)`` pairs, and writes the result to a CSV that the existing
:class:`FrequencyService` reads unchanged.

Per the v1 design decisions:
- Single-source: the output CSV overwrites the configured frequency path.
- Term-only key: reading is intentionally discarded; on collisions, ``min(rank)``
  wins so the user always sees the most-favourable rank for a homograph.
- Rank-based only: ``frequencyMode == "occurrence-based"`` dicts are rejected
  rather than silently re-ranked.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from anki_miner.exceptions import SetupError
from anki_miner.services.dictionary.zip_safety import validate_zip_safe

logger = logging.getLogger(__name__)

ProgressFn = Callable[[int, int, str], None]


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
    if not zip_path.exists():
        raise SetupError(f"Yomitan frequency zip not found: {zip_path}")

    with tempfile.TemporaryDirectory(prefix="anki_miner_yomitan_freq_") as tmp:
        tmp_path = Path(tmp)
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                validate_zip_safe(zf, tmp_path)
                zf.extractall(tmp_path)
        except zipfile.BadZipFile as e:
            raise SetupError(f"Corrupt zip file: {e}") from e

        index_file = tmp_path / "index.json"
        if not index_file.exists():
            raise SetupError("Zip missing required index.json")

        try:
            index = json.loads(index_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise SetupError(f"Invalid index.json: {e}") from e

        title = str(index.get("title", "")).strip()
        revision = str(index.get("revision", "")).strip()
        if not title:
            raise SetupError("index.json missing required 'title'")

        frequency_mode = str(index.get("frequencyMode", "")).strip()
        if frequency_mode == "occurrence-based":
            raise SetupError(
                f"'{title}' is an occurrence-based frequency dictionary. "
                "anki_miner only supports rank-based dictionaries (e.g. JPDB, BCCWJ). "
                "Use a rank-based dict, or convert this one externally before importing."
            )

        meta_files = sorted(tmp_path.glob("term_meta_bank_*.json"))
        if not meta_files:
            raise SetupError(
                "Zip contains no frequency data (term_meta_bank_*.json missing). "
                "This is likely a definition-only dictionary; import it via "
                "Settings → Dictionary → Add Dictionary instead."
            )

        ranks: dict[str, int] = {}
        skipped_display_only = 0

        for file_idx, meta_file in enumerate(meta_files, 1):
            if cancel_check and cancel_check():
                raise SetupError("Import cancelled")
            try:
                entries = json.loads(meta_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                raise SetupError(f"Invalid {meta_file.name}: {e}") from e

            for entry in entries:
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

            if progress:
                progress(file_idx, len(meta_files), f"Imported {meta_file.name}")

        if not ranks:
            raise SetupError(
                f"'{title}' yielded no usable frequency entries (skipped "
                f"{skipped_display_only} display-only entries). "
                "The dictionary may use an unsupported data format."
            )

        _atomic_write_csv(dest_csv, ranks)

        if progress:
            progress(len(meta_files), len(meta_files), "Done")

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


def _normalize_freq_rank(data: Any) -> int | None:
    """Extract an integer rank from any of Yomitan's five ``freq`` data shapes.

    Returns ``None`` for display-only entries (e.g. ``"①"``) that carry no
    integer rank — callers count these separately so they can be surfaced to
    the user.
    """
    if isinstance(data, bool):
        # bool is a subclass of int; reject before the int branch below.
        return None

    if isinstance(data, int):
        return data

    if isinstance(data, str):
        return _try_int(data)

    if isinstance(data, dict):
        # Outer envelope with `reading` + `frequency` (BCCWJ-style entries).
        # Per single-source / term-only-key decision, the reading itself is
        # discarded — we recurse into `frequency` and keep min(rank) on
        # collision.
        if "frequency" in data:
            return _normalize_freq_rank(data["frequency"])
        # Inner `GenericFrequencyData`: `{value, displayValue?}`.
        if "value" in data:
            value = data["value"]
            if isinstance(value, bool):
                return None
            if isinstance(value, int):
                return value
            if isinstance(value, str):
                return _try_int(value)

    return None


def _try_int(s: str) -> int | None:
    """Best-effort string→int conversion that tolerates surrounding whitespace.

    Returns ``None`` for display-only markers like ``"①"`` or ``"高"`` that
    have no integer interpretation.
    """
    s = s.strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _atomic_write_csv(dest_csv: Path, ranks: dict[str, int]) -> None:
    """Write ``term,rank`` rows to ``dest_csv`` atomically.

    Stages to a sibling ``.tmp`` file then ``os.replace`` so a crash mid-write
    leaves the user's existing ``frequency.csv`` intact.
    """
    dest_csv.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_csv.with_suffix(dest_csv.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["term", "rank"])
        for term, rank in sorted(ranks.items(), key=lambda kv: kv[1]):
            writer.writerow([term, rank])
    os.replace(tmp_path, dest_csv)
