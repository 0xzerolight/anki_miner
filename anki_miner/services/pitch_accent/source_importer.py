"""Import a pitch accent source (Yomitan zip or CSV/TSV) into a per-source index.

A "pitch source" is one pitch accent dictionary the user wants in the
first-hit-wins chain. This importer mirrors the frequency source importer: it
builds a per-source ``index.sqlite`` (plus ``meta.json`` sidecar) under
``<dest_root>/<source_id>/``, staging into a ``.staging-*`` dir and atomically
renaming on success, and copies the original input file alongside the index so
a later "reimport" can re-run without the user re-picking the file.

Two input shapes are supported, dispatched by suffix:

* ``.zip`` — a Yomitan pitch dictionary (``term_meta_bank_*.json`` with
  ``mode == "pitch"`` rows). Row extraction is shared with the legacy importer
  (:func:`~anki_miner.services.pitch_accent.yomitan_pitch_importer.extract_pitch_rows`).
* ``.csv`` / ``.tsv`` / ``.txt`` — a pitch CSV in either
  ``reading,term,pattern[,nasal,devoice]`` or ``term,reading,pattern`` order
  (Kanjium accents.txt uses the latter). Delimiter and column order are
  detected once per file; legacy 3-col and anomalous tail-rejoin rows use the
  shared :func:`~anki_miner.services.pitch_accent_service._parse_pitch_row`).

First occurrence wins per ``(kanji, reading)`` in both paths, matching the
legacy single-CSV loader's semantics.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from anki_miner.exceptions import OperationCancelled, SetupError
from anki_miner.services._sqlite_index import (
    language_identity,
    prove_owned_slot,
    read_slot_language,
    resolve_auto_store_id,
    resolve_managed_slot,
    write_ownership_marker,
)
from anki_miner.services._staging import promote_staged_dir, repair_managed_slot
from anki_miner.services.pitch_accent import storage
from anki_miner.services.pitch_accent.yomitan_pitch_importer import (
    extract_pitch_rows,
)
from anki_miner.services.pitch_accent_service import iter_pitch_csv_rows
from anki_miner.services.yomitan_meta_bank import (
    ProgressFn,
    open_yomitan_meta_banks,
)
from anki_miner.utils.robust_fs import robust_rmtree
from anki_miner.utils.slug import slugify

logger = logging.getLogger(__name__)

PITCH_SOURCE_SUFFIXES = (".zip", ".csv", ".tsv", ".txt")
_ZIP_SUFFIXES = frozenset(PITCH_SOURCE_SUFFIXES[:1])
_CSV_SUFFIXES = frozenset(PITCH_SOURCE_SUFFIXES[1:])


@dataclass(frozen=True)
class PitchSourceImportResult:
    """Outcome of a successful pitch-source import."""

    source_id: str
    source_name: str
    source_revision: str
    format: str
    entry_count: int
    skipped_display_only: int
    # Structurally-malformed meta-bank entries skipped during a zip import
    # (always 0 for CSV/TSV sources). Surfaced to the user so a reduced import
    # doesn't pass unnoticed.
    skipped_malformed: int = 0


def import_pitch_source(
    input_path: Path,
    dest_root: Path,
    *,
    source_id: str | None = None,
    source_name: str | None = None,
    progress: ProgressFn | None = None,
    cancel_check: Callable[[], bool] | None = None,
    overwrite: bool = False,
    before_promote: Callable[[], None] | None = None,
    language: str = "ja",
) -> PitchSourceImportResult:
    """Import ``input_path`` into ``dest_root/<source_id>/index.sqlite``.

    Args:
        input_path: A Yomitan pitch ``.zip`` or a ``.csv``/``.tsv``/``.txt``
            pitch file (``reading,term,pattern[,nasal,devoice]`` or
            ``term,reading,pattern``).
        dest_root: Folder under which ``<source_id>/`` is created (typically
            ``~/.anki_miner/pitch/``).
        source_id: Explicit on-disk id. When omitted, derived from the Yomitan
            ``index.json`` title (zip) or the CSV filename stem, then slugified.
        source_name: Explicit human display name. When omitted, a CSV derives it
            from the filename stem (used by reimport to preserve the existing
            display name). Ignored for zips (their title comes from
            ``index.json``).
        progress: Optional ``(current, total, message)`` callback.
        cancel_check: Optional zero-arg predicate; if it returns True the import
            aborts (partial staging files are cleaned up).
        overwrite: If true, replace an existing same-id source atomically.
        before_promote: Optional last-moment guard run immediately before the
            staged directory replaces the managed slot.
        language: Mining language stamped into the index meta. Defaults to
            ``"ja"``, the pre-transition value for every existing caller.

    Raises:
        SetupError: On a missing/unsupported input, or a source that yields zero
            usable entries, or when the destination exists and overwrite is false.
    """
    if not input_path.exists():
        raise SetupError(f"Pitch source not found: {input_path}")

    suffix = input_path.suffix.lower()
    if suffix in _ZIP_SUFFIXES:
        return _import_zip(
            input_path,
            dest_root,
            source_id=source_id,
            progress=progress,
            cancel_check=cancel_check,
            overwrite=overwrite,
            before_promote=before_promote,
            language=language,
        )
    if suffix in _CSV_SUFFIXES:
        return _import_csv(
            input_path,
            dest_root,
            source_id=source_id,
            source_name=source_name,
            cancel_check=cancel_check,
            overwrite=overwrite,
            before_promote=before_promote,
            language=language,
        )
    raise SetupError(
        f"Unsupported pitch source '{input_path.name}'. "
        "Provide a Yomitan .zip or a reading,term / term,reading pitch CSV/TSV."
    )


def repair_pitch_source(
    input_path: Path,
    dest_root: Path,
    *,
    source_id: str,
    source_name: str,
    progress: ProgressFn | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> PitchSourceImportResult:
    """Explicitly repair ``source_id``, retaining an invalid prior slot as quarantine."""
    # Read the stamp before the rebuild: repair_managed_slot may quarantine the
    # slot, and a re-import would otherwise fall back to the "ja" default.
    language = read_slot_language(dest_root / source_id)
    return repair_managed_slot(
        input_path,
        dest_root,
        source_id,
        "pitch",
        lambda source, overwrite: import_pitch_source(
            source,
            dest_root,
            source_id=source_id,
            source_name=source_name,
            progress=progress,
            cancel_check=cancel_check,
            overwrite=overwrite,
            language=language,
        ),
    )


def _import_zip(
    zip_path: Path,
    dest_root: Path,
    *,
    source_id: str | None,
    progress: ProgressFn | None,
    cancel_check: Callable[[], bool] | None,
    overwrite: bool,
    before_promote: Callable[[], None] | None,
    language: str,
) -> PitchSourceImportResult:
    with open_yomitan_meta_banks(zip_path, kind="pitch") as banks:
        entries_out, skipped_display_only = extract_pitch_rows(banks, progress=progress, cancel_check=cancel_check)
        title = banks.title
        revision = banks.revision
        resolved_id = source_id or resolve_auto_store_id(
            dest_root,
            _derive_source_id(title),
            "pitch",
            {"source_name": title, "source_revision": revision, **language_identity(language)},
        )

        if not entries_out:
            raise SetupError(
                f"'{title}' yielded no usable pitch entries (skipped "
                f"{skipped_display_only} display-only entries). "
                "The dictionary may use an unsupported data format."
            )

        # Stored rows in the CSV column order, sorted by (reading, kanji) to
        # match the legacy CSV importer's stable output order.
        rows = (
            (reading, kanji, pattern, nasal_field, devoice_field)
            for (kanji, reading), (pattern, nasal_field, devoice_field) in sorted(
                entries_out.items(), key=lambda kv: (kv[0][1], kv[0][0])
            )
        )
        result = _finalize(
            input_path=zip_path,
            dest_root=dest_root,
            source_id=resolved_id,
            source_name=title,
            source_revision=revision,
            fmt="yomitan-pitch",
            rows=rows,
            entry_count=len(entries_out),
            skipped_display_only=skipped_display_only,
            skipped_malformed=banks.skipped_malformed,
            cancel_check=cancel_check,
            overwrite=overwrite,
            before_promote=before_promote,
            language=language,
        )

    logger.info(
        "Imported %d pitch entries from '%s' (revision '%s') as source '%s', skipped %d display-only, %d malformed",
        result.entry_count,
        title,
        revision,
        result.source_id,
        skipped_display_only,
        result.skipped_malformed,
    )
    return result


def _import_csv(
    csv_path: Path,
    dest_root: Path,
    *,
    source_id: str | None,
    source_name: str | None = None,
    cancel_check: Callable[[], bool] | None,
    overwrite: bool,
    before_promote: Callable[[], None] | None,
    language: str,
) -> PitchSourceImportResult:
    stem = csv_path.stem
    # Honor an explicit display name (reimport passes the existing meta name);
    # otherwise derive from the filename stem — preserving it here keeps
    # reimport from collapsing the label to the generic "source.csv" stem.
    resolved_name = source_name if source_name else stem
    resolved_id = source_id or resolve_auto_store_id(
        dest_root,
        _derive_source_id(stem),
        "pitch",
        {"source_name": resolved_name, "source_revision": "", **language_identity(language)},
    )

    # key = (kanji, reading) -> (pattern, nasal, devoice); first occurrence
    # wins, matching the legacy single-CSV loader and the zip path.
    entries_out: dict[tuple[str, str], storage.PitchStorageRow] = {}
    for parsed in iter_pitch_csv_rows(csv_path):
        if cancel_check is not None and cancel_check():
            raise OperationCancelled("Import cancelled")
        if not (parsed.kanji or parsed.reading):
            continue
        key = (parsed.kanji, parsed.reading)
        if key not in entries_out:
            entry = parsed.entry
            entries_out[key] = (
                parsed.reading,
                parsed.kanji,
                entry.pattern,
                ",".join(str(n) for n in entry.nasal),
                ",".join(str(d) for d in entry.devoice),
            )

    if not entries_out:
        raise SetupError(
            f"'{csv_path.name}' yielded no usable pitch entries. "
            "Expected reading,term or term,reading pitch columns (CSV/TSV, 3 or 5 columns)."
        )

    result = _finalize(
        input_path=csv_path,
        dest_root=dest_root,
        source_id=resolved_id,
        source_name=resolved_name,
        source_revision="",
        fmt="csv",
        rows=entries_out.values(),
        entry_count=len(entries_out),
        skipped_display_only=0,
        cancel_check=cancel_check,
        overwrite=overwrite,
        before_promote=before_promote,
        language=language,
    )
    logger.info(
        "Imported %d pitch entries from CSV '%s' as source '%s'",
        result.entry_count,
        csv_path.name,
        result.source_id,
    )
    return result


def _finalize(
    *,
    input_path: Path,
    dest_root: Path,
    source_id: str,
    source_name: str,
    source_revision: str,
    fmt: str,
    rows: Iterable[storage.PitchStorageRow],
    entry_count: int,
    skipped_display_only: int,
    skipped_malformed: int = 0,
    cancel_check: Callable[[], bool] | None,
    overwrite: bool,
    before_promote: Callable[[], None] | None,
    language: str,
) -> PitchSourceImportResult:
    """Build the index under a staging dir, then atomically promote it.

    Copies the original input alongside ``index.sqlite`` (``source.zip`` /
    ``source.csv``) for later reimport.
    """
    try:
        final_path = resolve_managed_slot(dest_root, source_id)
    except ValueError as exc:
        raise SetupError(str(exc)) from exc
    final_path.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(final_path):
        if not overwrite:
            raise SetupError(f"Pitch source '{source_id}' already exists")
        if not prove_owned_slot(final_path.parent, source_id, "pitch"):
            raise SetupError(
                f"Pitch source '{source_id}' exists but is not an Anki Miner-managed pitch source; "
                "refusing to overwrite it"
            )

    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=final_path.parent))
    try:
        write_ownership_marker(staging, source_id, "pitch")
        db_path = staging / "index.sqlite"
        meta = {
            "schema_version": str(storage.SCHEMA_VERSION),
            "format": fmt,
            "source_name": source_name,
            "source_revision": source_revision,
            "import_date": datetime.now(UTC).isoformat(),
            "entry_count": str(entry_count),
            "language": language,
        }
        storage.build_index(db_path, rows, meta)

        # Persist the source file so a later "reimport" can rebuild without the
        # user re-picking it (mirrors the frequency importer's source.zip).
        source_copy_name = "source" + input_path.suffix.lower()
        shutil.copy2(input_path, staging / source_copy_name)

        if cancel_check is not None and cancel_check():
            raise OperationCancelled("Import cancelled")

        try:
            promote_staged_dir(
                staging,
                final_path,
                mover=shutil.move,
                overwrite=overwrite,
                before_promote=before_promote,
            )
        except FileExistsError as exc:
            raise SetupError(f"Pitch source '{source_id}' already exists") from exc
    finally:
        # On success the staging dir was moved away; clean up on any failure
        # so a partial import does not orphan a .staging-* dir in dest_root.
        robust_rmtree(staging, mode="outcome")

    return PitchSourceImportResult(
        source_id=source_id,
        source_name=source_name,
        source_revision=source_revision,
        format=fmt,
        entry_count=entry_count,
        skipped_display_only=skipped_display_only,
        skipped_malformed=skipped_malformed,
    )


def _derive_source_id(name: str) -> str:
    """Slugify a title / filename stem into an on-disk source id."""
    return slugify(name, fallback="source")
