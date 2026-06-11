"""Yomitan-format pitch-accent zip → CSV importer.

Yomitan pitch dictionaries (e.g. NHK, Kanjium-derived) ship as a zip containing
``index.json`` plus one or more ``term_meta_bank_*.json`` files. Each meta-bank
is a flat JSON array of ``[term, mode, data]`` triples; this importer extracts
only ``mode == "pitch"`` rows and writes them to a ``reading,kanji,pattern`` CSV
that the existing :class:`PitchAccentService` reads unchanged.

Structural mirror of :mod:`anki_miner.services.frequency.yomitan_freq_importer`
— same index validation, atomic write, progress/cancel surface, and
``skipped_display_only`` accounting for entries that yielded no usable pitches.
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
from typing import Callable

from anki_miner.exceptions import SetupError
from anki_miner.services.dictionary.zip_safety import validate_zip_safe

logger = logging.getLogger(__name__)

ProgressFn = Callable[[int, int, str], None]


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
    if not zip_path.exists():
        raise SetupError(f"Yomitan pitch zip not found: {zip_path}")

    with tempfile.TemporaryDirectory(prefix="anki_miner_yomitan_pitch_") as tmp:
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

        # Yomitan format v1/v2 use different term_meta_bank schemas than v3.
        # Strict equality with 3 surfaces the mismatch clearly; revisit
        # if/when Yomitan ships a v4 we want to accept.
        format_version = index.get("format")
        if format_version != 3:
            raise SetupError(
                f"'{title}' uses unsupported Yomitan format version {format_version!r}. "
                "anki_miner supports format version 3 only. "
                "Re-download from a current Yomitan source."
            )

        meta_files = sorted(tmp_path.glob("term_meta_bank_*.json"))
        if not meta_files:
            raise SetupError(
                "Zip contains no pitch data (term_meta_bank_*.json missing). "
                "This is likely a definition-only dictionary; import it via "
                "Settings → Dictionary → Add Dictionary instead."
            )

        # Key on (kanji_or_term, reading) so homographs with distinct readings
        # both survive. First occurrence wins to match PitchAccentService.load.
        entries_out: dict[tuple[str, str], str] = {}
        skipped_display_only = 0

        for file_idx, meta_file in enumerate(meta_files, 1):
            if cancel_check and cancel_check():
                raise SetupError("Import cancelled")
            try:
                bank = json.loads(meta_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                raise SetupError(f"Invalid {meta_file.name}: {e}") from e

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

            if progress:
                progress(file_idx, len(meta_files), f"Imported {meta_file.name}")

        if not entries_out:
            raise SetupError(
                f"'{title}' yielded no usable pitch entries (skipped "
                f"{skipped_display_only} display-only entries). "
                "The dictionary may use an unsupported data format."
            )

        _atomic_write_csv(dest_csv, entries_out)

        if progress:
            progress(len(meta_files), len(meta_files), "Done")

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


def _atomic_write_csv(dest_csv: Path, entries: dict[tuple[str, str], str]) -> None:
    """Write ``reading,kanji,pattern`` rows to ``dest_csv`` atomically.

    Stages to a sibling ``.tmp`` file then ``os.replace`` so a crash mid-write
    leaves the user's existing ``pitch_accent.csv`` intact.
    """
    dest_csv.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_csv.with_suffix(dest_csv.suffix + ".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["reading", "kanji", "pattern"])
            for (kanji, reading), pattern in sorted(entries.items(), key=lambda kv: (kv[0][1], kv[0][0])):
                writer.writerow([reading, kanji, pattern])
        os.replace(tmp_path, dest_csv)
    finally:
        # A failure mid-rows raises before os.replace, orphaning the .tmp in
        # ~/.anki_miner. Unlink it; on success it's already gone (os.replace
        # consumed it) and missing_ok makes the unlink a no-op.
        tmp_path.unlink(missing_ok=True)
