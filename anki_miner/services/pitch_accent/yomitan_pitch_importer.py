"""Yomitan-format pitch-accent zip → CSV importer.

Yomitan pitch dictionaries (e.g. NHK, Kanjium-derived) ship as a zip containing
``index.json`` plus one or more ``term_meta_bank_*.json`` files. Each meta-bank
is a flat JSON array of ``[term, mode, data]`` triples; this importer extracts
only ``mode == "pitch"`` rows and writes them to a ``reading,kanji,pattern`` CSV
that the existing :class:`PitchAccentService` reads unchanged.

Shared zip extraction, index validation, the strict ``format == 3`` gate, the
per-file progress/cancel loop, and the atomic CSV write live in
:mod:`anki_miner.services.yomitan_meta_bank`; only the pitch-specific
``data`` normalization remains here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from anki_miner.exceptions import SetupError
from anki_miner.services.yomitan_meta_bank import (
    ProgressFn,
    atomic_write_csv,
    open_yomitan_meta_banks,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class YomitanPitchImportResult:
    """Outcome of a successful Yomitan pitch-accent import."""

    source_name: str
    source_revision: str
    entry_count: int
    skipped_display_only: int


def import_yomitan_pitch_zip(
    zip_path: Path,
    dest_csv: Path,
    *,
    progress: ProgressFn | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> YomitanPitchImportResult:
    """Import a Yomitan pitch-accent zip into ``dest_csv``.

    Args:
        zip_path: Path to the Yomitan-format pitch zip.
        dest_csv: Output CSV path. Written atomically.
        progress: Optional ``(current, total, message)`` callback fired per
            ``term_meta_bank_*.json`` file processed.
        cancel_check: Optional zero-arg predicate; if it returns True between
            files, the import aborts and the existing ``dest_csv`` is left
            untouched.

    Raises:
        SetupError: On invalid input, missing meta banks, corrupt JSON, or
            unsafe zip paths.
    """
    with open_yomitan_meta_banks(zip_path, kind="pitch") as banks:
        # Key on (kanji_or_term, reading) so homographs with distinct readings
        # both survive. First occurrence wins to match PitchAccentService.load.
        entries_out: dict[tuple[str, str], str] = {}
        skipped_display_only = 0

        for bank in banks.iter_banks(progress=progress, cancel_check=cancel_check):
            for entry in bank:
                if not isinstance(entry, list) or len(entry) < 3:
                    continue
                if entry[1] != "pitch":
                    continue
                term = str(entry[0]).strip() if entry[0] is not None else ""
                if not term:
                    continue

                data = entry[2]
                if not isinstance(data, dict):
                    skipped_display_only += 1
                    continue

                reading_raw = data.get("reading", "")
                reading = str(reading_raw).strip() if reading_raw is not None else ""
                pitches = data.get("pitches", [])
                if not isinstance(pitches, list):
                    pitches = []

                positions = [
                    p["position"]
                    for p in pitches
                    if isinstance(p, dict)
                    and isinstance(p.get("position"), int)
                    and not isinstance(p.get("position"), bool)
                ]

                if not reading or not positions:
                    skipped_display_only += 1
                    continue

                kanji = term if term != reading else ""
                pattern = ",".join(str(p) for p in positions)
                key = (kanji, reading)
                if key not in entries_out:
                    entries_out[key] = pattern

        title = banks.title
        revision = banks.revision

        if not entries_out:
            raise SetupError(
                f"'{title}' yielded no usable pitch entries (skipped "
                f"{skipped_display_only} display-only entries). "
                "The dictionary may use an unsupported data format."
            )

        # reading,kanji,pattern — sorted by (reading, kanji) for stable output.
        rows = [
            (reading, kanji, pattern)
            for (kanji, reading), pattern in sorted(entries_out.items(), key=lambda kv: (kv[0][1], kv[0][0]))
        ]
        atomic_write_csv(dest_csv, ["reading", "kanji", "pattern"], rows)

    logger.info(
        "Imported %d pitch entries from '%s' (revision '%s'), skipped %d display-only",
        len(entries_out),
        title,
        revision,
        skipped_display_only,
    )

    return YomitanPitchImportResult(
        source_name=title,
        source_revision=revision,
        entry_count=len(entries_out),
        skipped_display_only=skipped_display_only,
    )
