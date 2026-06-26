"""Import a frequency source (Yomitan zip or plain CSV/TSV) into a per-source index.

A "frequency source" is one rank list the user wants to additively layer with
others. This importer mirrors the dictionary and audio-pack import flows: it
builds a per-source ``index.sqlite`` (plus ``meta.json`` sidecar) under
``<dest_root>/<source_id>/``, staging into a ``.staging-*`` dir and atomically
renaming on success, and copies the original input file alongside the index so a
later "reimport" can re-run without the user re-picking the file.

Two input shapes are supported, dispatched by suffix:

* ``.zip`` — a Yomitan ``frequency`` meta-bank dictionary. ``occurrence-based``
  dicts are rejected (rank-based only). BCCWJ-style envelope readings are kept
  in the ``reading`` column; otherwise reading is ``NULL``. On a
  ``(term, reading)`` collision the smaller (better) rank wins.
* ``.csv`` / ``.tsv`` / ``.txt`` — a plain rank list. Delimiter is auto-detected,
  a header row is skipped, and rows are parsed with the shared
  :func:`~anki_miner.services.frequency.csv_parse._extract_word_rank`. A third
  column (``term, reading, rank``) is captured as the reading. First occurrence
  wins per ``(term, reading)`` (matching ``FrequencyService`` load semantics).
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from anki_miner.exceptions import SetupError
from anki_miner.services.frequency import storage
from anki_miner.services.frequency.csv_parse import (
    _extract_word_rank,
    _is_word_first_header,
    extract_envelope_reading,
    normalize_freq_rank,
)
from anki_miner.services.yomitan_meta_bank import (
    ProgressFn,
    open_yomitan_meta_banks,
)
from anki_miner.utils.csv_utils import detect_delimiter, is_header_row

logger = logging.getLogger(__name__)

# index.json is tiny; cap how much we pull into memory when peeking at a zip's
# title for source_id derivation so a small zip carrying a multi-GB index.json
# cannot OOM. 8 MiB is orders of magnitude beyond any legitimate index.json.
_MAX_INDEX_JSON_BYTES = 8 * 1024 * 1024

_ZIP_SUFFIXES = {".zip"}
_CSV_SUFFIXES = {".csv", ".tsv", ".txt"}


@dataclass(frozen=True)
class FreqSourceImportResult:
    """Outcome of a successful frequency-source import."""

    source_id: str
    source_name: str
    source_revision: str
    format: str
    entry_count: int
    skipped_display_only: int


def import_frequency_source(
    input_path: Path,
    dest_root: Path,
    *,
    source_id: str | None = None,
    progress: ProgressFn | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> FreqSourceImportResult:
    """Import ``input_path`` into ``dest_root/<source_id>/index.sqlite``.

    Args:
        input_path: A Yomitan frequency ``.zip`` or a plain ``.csv``/``.tsv``/
            ``.txt`` rank list.
        dest_root: Folder under which ``<source_id>/`` is created (typically
            ``~/.anki_miner/freq_sources/``).
        source_id: Explicit on-disk id. When omitted, derived from the Yomitan
            ``index.json`` title (zip) or the CSV filename stem, then slugified.
        progress: Optional ``(current, total, message)`` callback.
        cancel_check: Optional zero-arg predicate; if it returns True the import
            aborts (partial staging files are cleaned up by the temp dir).

    Raises:
        SetupError: On a missing/unsupported input, an occurrence-based Yomitan
            dict, or a source that yields zero usable entries.
    """
    if not input_path.exists():
        raise SetupError(f"Frequency source not found: {input_path}")

    suffix = input_path.suffix.lower()
    if suffix in _ZIP_SUFFIXES:
        return _import_zip(
            input_path,
            dest_root,
            source_id=source_id,
            progress=progress,
            cancel_check=cancel_check,
        )
    if suffix in _CSV_SUFFIXES:
        return _import_csv(input_path, dest_root, source_id=source_id)
    raise SetupError(
        f"Unsupported frequency source '{input_path.name}'. " "Provide a Yomitan .zip or a .csv/.tsv/.txt rank list."
    )


def _import_zip(
    zip_path: Path,
    dest_root: Path,
    *,
    source_id: str | None,
    progress: ProgressFn | None,
    cancel_check: Callable[[], bool] | None,
) -> FreqSourceImportResult:
    with open_yomitan_meta_banks(zip_path, kind="frequency") as banks:
        if banks.index.frequency_mode == "occurrence-based":
            raise SetupError(
                f"'{banks.title}' is an occurrence-based frequency dictionary. "
                "anki_miner only supports rank-based dictionaries (e.g. JPDB, BCCWJ). "
                "Use a rank-based dict, or convert this one externally before importing."
            )

        title = banks.title
        revision = banks.revision
        resolved_id = source_id or _derive_source_id(title)

        # key = (term, reading) -> best (min) rank
        ranks: dict[tuple[str, str | None], int] = {}
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

                data = entry[2]
                rank = normalize_freq_rank(data)
                if rank is None:
                    skipped_display_only += 1
                    continue

                reading = extract_envelope_reading(data)
                key = (term, reading)
                existing = ranks.get(key)
                if existing is None or rank < existing:
                    ranks[key] = rank

        if not ranks:
            raise SetupError(
                f"'{title}' yielded no usable frequency entries (skipped "
                f"{skipped_display_only} display-only entries). "
                "The dictionary may use an unsupported data format."
            )

        # Sorted by rank for stable, human-scannable storage order.
        rows = [(term, reading, rank) for (term, reading), rank in sorted(ranks.items(), key=lambda kv: kv[1])]

        result = _finalize(
            input_path=zip_path,
            dest_root=dest_root,
            source_id=resolved_id,
            source_name=title,
            source_revision=revision,
            fmt="yomitan-freq",
            rows=rows,
            entry_count=len(ranks),
            skipped_display_only=skipped_display_only,
        )

    logger.info(
        "Imported %d frequency entries from '%s' (revision '%s') as source '%s', skipped %d display-only",
        result.entry_count,
        title,
        revision,
        result.source_id,
        skipped_display_only,
    )
    return result


def _import_csv(
    csv_path: Path,
    dest_root: Path,
    *,
    source_id: str | None,
) -> FreqSourceImportResult:
    stem = csv_path.stem
    resolved_id = source_id or _derive_source_id(stem)

    # key = (term, reading) -> rank; first occurrence wins (matches
    # FrequencyService load semantics, which keeps the first rank per word).
    ranks: dict[tuple[str, str | None], int] = {}
    try:
        with open(csv_path, encoding="utf-8") as f:
            sample = f.read(4096)
            f.seek(0)
            delimiter = detect_delimiter(sample)

            import csv as _csv

            reader = _csv.reader(f, delimiter=delimiter)
            first_row = True
            word_first = False
            for row in reader:
                if len(row) < 2:
                    continue
                if first_row:
                    first_row = False
                    if is_header_row(row):
                        word_first = _is_word_first_header(row)
                        continue

                word, rank = _extract_word_rank(row, word_first=word_first)
                if not word or rank is None:
                    continue

                reading = _csv_reading(row, word)
                key = (word, reading)
                if key not in ranks:
                    ranks[key] = rank
    except OSError as e:
        raise SetupError(f"Error reading frequency source '{csv_path.name}': {e}") from e

    if not ranks:
        raise SetupError(
            f"'{csv_path.name}' yielded no usable frequency entries. "
            "Expected a CSV/TSV with a word column and a numeric rank column."
        )

    rows = [(term, reading, rank) for (term, reading), rank in sorted(ranks.items(), key=lambda kv: kv[1])]

    result = _finalize(
        input_path=csv_path,
        dest_root=dest_root,
        source_id=resolved_id,
        source_name=stem,
        source_revision="",
        fmt="csv",
        rows=rows,
        entry_count=len(ranks),
        skipped_display_only=0,
    )
    logger.info(
        "Imported %d frequency entries from CSV '%s' as source '%s'",
        result.entry_count,
        csv_path.name,
        result.source_id,
    )
    return result


def _csv_reading(row: list[str], word: str) -> str | None:
    """Return a reading from a ``term, reading, rank`` row, else ``None``.

    Only a 3+-column row whose col-0 is the matched word carries a reading; the
    reading is col-1. If col-1 is empty or numeric (i.e. the file is really a
    ``word, rank`` 2-col list padded with a blank), no reading is captured.
    """
    if len(row) < 3:
        return None
    if not row or row[0].strip() != word:
        return None
    candidate = row[1].strip()
    if not candidate:
        return None
    # A purely-numeric col-1 is the rank, not a reading (the rank scan in
    # _extract_word_rank already consumed it).
    try:
        int(candidate)
        return None
    except ValueError:
        return candidate


def _finalize(
    *,
    input_path: Path,
    dest_root: Path,
    source_id: str,
    source_name: str,
    source_revision: str,
    fmt: str,
    rows: list[storage.FreqRow],
    entry_count: int,
    skipped_display_only: int,
) -> FreqSourceImportResult:
    """Build the index under a staging dir, then atomically promote it.

    Copies the original input alongside ``index.sqlite`` (``source.zip`` /
    ``source.csv``) for later reimport, overwriting any same-id source.
    """
    dest_root.mkdir(parents=True, exist_ok=True)
    final_path = dest_root / source_id

    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=dest_root))
    try:
        db_path = staging / "index.sqlite"
        meta = {
            "schema_version": str(storage.SCHEMA_VERSION),
            "format": fmt,
            "source_name": source_name,
            "source_revision": source_revision,
            "import_date": datetime.now(UTC).isoformat(),
            "entry_count": str(entry_count),
        }
        storage.build_index(db_path, rows, meta)

        # Persist the source file so a later "reimport" can rebuild without the
        # user re-picking it (mirrors the dict importer's source.zip).
        source_copy_name = "source" + input_path.suffix.lower()
        shutil.copy2(input_path, staging / source_copy_name)

        # Atomic-ish promote: replace any existing same-id source.
        if final_path.exists():
            backup = final_path.with_name(final_path.name + ".bak-" + datetime.now(UTC).strftime("%Y%m%d%H%M%S%f"))
            final_path.rename(backup)
            try:
                shutil.move(str(staging), str(final_path))
            except Exception:
                if final_path.exists():
                    shutil.rmtree(final_path, ignore_errors=True)
                if not final_path.exists():
                    backup.rename(final_path)
                raise
            shutil.rmtree(backup, ignore_errors=True)
        else:
            shutil.move(str(staging), str(final_path))
    finally:
        # On success the staging dir was moved away; clean up on any failure
        # so a partial import does not orphan a .staging-* dir in dest_root.
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)

    return FreqSourceImportResult(
        source_id=source_id,
        source_name=source_name,
        source_revision=source_revision,
        format=fmt,
        entry_count=entry_count,
        skipped_display_only=skipped_display_only,
    )


def _derive_source_id(name: str) -> str:
    """Slugify a title / filename stem into an on-disk source id.

    Mirrors the dictionary importer's slug rule: lowercase ASCII, non-ASCII
    code points become ``u<hex>``, runs of other chars collapse to ``-``.
    """
    return _slug(name)


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    """ASCII slug suitable for a directory name. CJK falls through as hex codepoints."""
    text = text.strip().lower()
    parts: list[str] = []
    buf: list[str] = []
    for ch in text:
        if ord(ch) < 128:
            buf.append(ch)
        else:
            if buf:
                parts.append("".join(buf))
                buf.clear()
            parts.append(f"u{ord(ch):x}")
    if buf:
        parts.append("".join(buf))
    slug = _SLUG_RE.sub("-", "-".join(parts)).strip("-")
    return slug or "source"


def derive_source_id_from_zip(zip_path: Path) -> str:
    """Peek at a Yomitan zip's ``index.json`` title → derived ``source_id``.

    Used to validate a user-picked zip against a stale reimport slot without
    invoking the full importer.

    Raises:
        SetupError: zip missing, corrupt, missing ``index.json``, or
            ``index.json`` lacks a non-empty ``title``.
    """
    if not zip_path.exists():
        raise SetupError(f"Yomitan zip not found: {zip_path}")
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            try:
                info = zf.getinfo("index.json")
            except KeyError as e:
                raise SetupError("Zip missing required index.json") from e
            if info.file_size > _MAX_INDEX_JSON_BYTES:
                raise SetupError(
                    f"index.json is implausibly large ({info.file_size:,} > {_MAX_INDEX_JSON_BYTES:,} bytes)"
                )
            with zf.open("index.json") as fp:
                raw_bytes = fp.read(_MAX_INDEX_JSON_BYTES + 1)
            if len(raw_bytes) > _MAX_INDEX_JSON_BYTES:
                raise SetupError(f"index.json exceeds the {_MAX_INDEX_JSON_BYTES:,}-byte cap")
            raw = raw_bytes.decode("utf-8")
    except zipfile.BadZipFile as e:
        raise SetupError(f"Corrupt zip file: {e}") from e

    try:
        index = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SetupError(f"Invalid index.json: {e}") from e

    title = str(index.get("title", "")).strip()
    if not title:
        raise SetupError("index.json missing required 'title'")
    return _derive_source_id(title)
