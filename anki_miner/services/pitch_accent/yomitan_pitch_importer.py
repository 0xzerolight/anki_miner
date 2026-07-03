"""Yomitan-format pitch-accent zip → CSV importer.

Yomitan pitch dictionaries (e.g. NHK, Kanjium-derived) ship as a zip containing
``index.json`` plus one or more ``term_meta_bank_*.json`` files. Each meta-bank
is a flat JSON array of ``[term, mode, data]`` triples; this importer extracts
only ``mode == "pitch"`` rows and writes them to a
``reading,kanji,pattern,nasal,devoice`` CSV that :class:`PitchAccentService`
reads. Integer *and* ``[HL]+`` mora-string positions are kept, and each pitch's
nasal/devoice mora positions are retained (each of pattern/nasal/devoice is one
csv.writer field, so an intra-field comma like ``0,2`` never shifts a column).

Shared zip extraction, index validation, the strict ``format == 3`` gate, the
per-file progress/cancel loop, and the atomic CSV write live in
:mod:`anki_miner.services.yomitan_meta_bank`; only the pitch-specific
``data`` normalization remains here.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from anki_miner.exceptions import SetupError
from anki_miner.services.yomitan_meta_bank import (
    ProgressFn,
    atomic_write_csv,
    open_yomitan_meta_banks,
)

logger = logging.getLogger(__name__)

# A pitch "position" may be an integer downstep OR an "[HL]+" mora string
# (Yomitan term-meta-bank v3 schema: position is ``integer | "^[HL]+$"``). Both
# are schema-legal; the earlier importer kept only the integer form and dropped
# H/L rows plus every nasal/devoice annotation.
_HL_PATTERN_RE = re.compile(r"^[HL]+$")


def _valid_position(value: Any) -> int | str | None:
    """Return a schema-legal pitch position (int >= 0 or "[HL]+"), else None.

    ``bool`` is rejected explicitly — ``isinstance(True, int)`` is True in
    Python and a JSON ``true`` must not pass as position 1.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and _HL_PATTERN_RE.match(value):
        return value
    return None


def _to_number_array(value: Any) -> list[int]:
    """Normalize a nasal/devoice field to a list of mora positions.

    Ported from Yomitan Translator._toNumberArray
    (ext/js/language/translator.js, upstream commit e2ed450): a bare integer
    becomes ``[n]``, a list is kept (non-int / bool members dropped), anything
    else becomes ``[]``.
    """
    if isinstance(value, bool):
        return []
    if isinstance(value, int):
        return [value]
    if isinstance(value, list):
        return [x for x in value if isinstance(x, int) and not isinstance(x, bool)]
    return []


@dataclass(frozen=True)
class YomitanPitchImportResult:
    """Outcome of a successful Yomitan pitch-accent import."""

    source_name: str
    source_revision: str
    entry_count: int
    skipped_display_only: int
    # Structurally-malformed meta-bank entries skipped during import. Surfaced
    # to the user so a reduced import doesn't pass unnoticed.
    skipped_malformed: int = 0


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
        # Value: (pattern, nasal_field, devoice_field) — each a single CSV field.
        entries_out: dict[tuple[str, str], tuple[str, str, str]] = {}
        skipped_display_only = 0

        for bank in banks.iter_banks(progress=progress, cancel_check=cancel_check):
            # Entries are already structurally validated by iter_banks (list,
            # arity >= 3, non-blank term); only the mode/data logic remains.
            for entry in bank:
                if entry[1] != "pitch":
                    continue
                term = str(entry[0]).strip()

                data = entry[2]
                if not isinstance(data, dict):
                    skipped_display_only += 1
                    continue

                reading_raw = data.get("reading", "")
                reading = str(reading_raw).strip() if reading_raw is not None else ""
                pitches = data.get("pitches", [])
                if not isinstance(pitches, list):
                    pitches = []

                positions: list[int | str] = []
                nasal: list[int] = []
                devoice: list[int] = []
                for p in pitches:
                    if not isinstance(p, dict):
                        continue
                    position = _valid_position(p.get("position"))
                    if position is None:
                        continue
                    positions.append(position)
                    nasal.extend(_to_number_array(p.get("nasal")))
                    devoice.extend(_to_number_array(p.get("devoice")))

                if not reading or not positions:
                    skipped_display_only += 1
                    continue

                kanji = term if term != reading else ""
                pattern = ",".join(str(p) for p in positions)
                # Dedupe positions preserving order (a bare integer nasal repeated
                # across pitches shouldn't double up in the merged field).
                nasal_field = ",".join(str(n) for n in dict.fromkeys(nasal))
                devoice_field = ",".join(str(d) for d in dict.fromkeys(devoice))
                key = (kanji, reading)
                if key not in entries_out:
                    entries_out[key] = (pattern, nasal_field, devoice_field)

        title = banks.title
        revision = banks.revision

        if not entries_out:
            raise SetupError(
                f"'{title}' yielded no usable pitch entries (skipped "
                f"{skipped_display_only} display-only entries). "
                "The dictionary may use an unsupported data format."
            )

        # reading,kanji,pattern,nasal,devoice — sorted by (reading, kanji).
        rows = [
            (reading, kanji, pattern, nasal_field, devoice_field)
            for (kanji, reading), (pattern, nasal_field, devoice_field) in sorted(
                entries_out.items(), key=lambda kv: (kv[0][1], kv[0][0])
            )
        ]
        atomic_write_csv(dest_csv, ["reading", "kanji", "pattern", "nasal", "devoice"], rows)

    logger.info(
        "Imported %d pitch entries from '%s' (revision '%s'), skipped %d display-only, %d malformed",
        len(entries_out),
        title,
        revision,
        skipped_display_only,
        banks.skipped_malformed,
    )

    return YomitanPitchImportResult(
        source_name=title,
        source_revision=revision,
        entry_count=len(entries_out),
        skipped_display_only=skipped_display_only,
        skipped_malformed=banks.skipped_malformed,
    )
