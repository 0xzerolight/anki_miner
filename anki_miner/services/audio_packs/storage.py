"""SQLite storage layer for audio packs.

This module owns the schema and all low-level read/write primitives.
Importers populate; fetchers query.

Note on connection idiom: This module deliberately uses explicit ``try/finally
conn.close()`` rather than ``with sqlite3.connect()`` as a context manager.
Reason: the sqlite3 ``with`` block commits/rolls back but does NOT close the
connection — we close explicitly so the db file is not held open across the
importer's staging-dir cleanup (matters on Windows where open file handles
block directory deletion).
"""

from __future__ import annotations

import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import anki_miner.services._sqlite_index as _sqlite_index
from anki_miner.services._sqlite_index import open_readonly as open_readonly
from anki_miner.services._sqlite_index import read_meta as read_meta
from anki_miner.services._sqlite_index import write_meta as write_meta

SCHEMA_VERSION = 2

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS entries (
    id          INTEGER PRIMARY KEY,
    expression  TEXT NOT NULL,
    reading     TEXT,
    source      TEXT NOT NULL,
    speaker     TEXT,
    display     TEXT,
    file        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_expr_reading ON entries(expression, reading);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""
# Surrogate scrubbing (dictionary storage Issue #67) is deliberately omitted:
# audio pack data is UTF-8 filenames/metadata, not converted XML.

# NULL-reading rows exist for forvo/legacy-jpod entries; empty reading happens
# when the miner has no reading for a word.  The WHERE clause below handles
# both cases as wildcards so callers never need to branch on reading presence.
# No LIMIT: fetchers want all candidate rows; dictionary storage's LIMIT 5 does not apply.
_LOOKUP_SQL = (
    "SELECT file, source, speaker, reading FROM entries "
    "WHERE expression = ? AND (? = '' OR reading IS NULL OR reading = ?) "
    "ORDER BY id"
)


@dataclass(frozen=True)
class AudioPackRow:
    """One importable entry. Mirrors the entries table schema."""

    expression: str
    source: str
    file: str
    reading: str | None = None
    speaker: str | None = None
    display: str | None = None


@dataclass(frozen=True)
class AudioEntry:
    """Result of a lookup query.

    ``reading`` carries the row's stored reading (None for wildcard rows,
    e.g. forvo/legacy-jpod) so the fetcher's wildcard-lookup ambiguity guard
    can count distinct readings; it is not part of the served audio identity.
    """

    file: str
    source: str
    speaker: str | None
    reading: str | None = None


def create_index(db_path: Path) -> None:
    """Create a fresh audio pack index at db_path. Idempotent (uses IF NOT EXISTS)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


def bulk_insert(
    db_path: Path,
    rows: Iterable[AudioPackRow],
    batch_size: int = 5000,
    *,
    on_malformed: Callable[[int], None] | None = None,
) -> int:
    """Insert rows in batched transactions. Returns total inserted.

    The sqlite3 `with` context manager commits/rolls back but does NOT close
    the connection — we close explicitly so the db file is not held open
    across the importer's staging-dir cleanup (matters on Windows).
    """
    total = 0
    skipped_malformed = 0
    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        batch: list[tuple] = []
        for row in rows:
            if row is None or not _valid_row(row):
                skipped_malformed += 1
                continue
            batch.append(
                (
                    unicodedata.normalize("NFC", row.expression),
                    unicodedata.normalize("NFC", row.reading) if row.reading is not None else None,
                    row.source,
                    row.speaker,
                    row.display,
                    row.file,
                )
            )
            if len(batch) >= batch_size:
                conn.executemany(
                    "INSERT INTO entries (expression, reading, source, speaker, display, file) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    batch,
                )
                total += len(batch)
                batch.clear()
        if batch:
            conn.executemany(
                "INSERT INTO entries (expression, reading, source, speaker, display, file) VALUES (?, ?, ?, ?, ?, ?)",
                batch,
            )
            total += len(batch)
        # All batches accumulate in one transaction; commit is atomic at end — no partial durability.
        conn.commit()
    finally:
        conn.close()
    if on_malformed is not None:
        on_malformed(skipped_malformed)
    return total


def _valid_row(row: AudioPackRow) -> bool:
    return (
        isinstance(row, AudioPackRow)
        and isinstance(row.expression, str)
        and bool(row.expression)
        and (row.reading is None or isinstance(row.reading, str))
        and isinstance(row.source, str)
        and (row.speaker is None or isinstance(row.speaker, str))
        and (row.display is None or isinstance(row.display, str))
        and isinstance(row.file, str)
        and bool(row.file)
    )


def read_meta_cached(db_path: Path) -> dict[str, str]:
    """Read meta rows via the ``meta.json`` sidecar when fresh, falling back to
    :func:`read_meta` when the sidecar is missing/stale/corrupt.

    Used by the registry to skip the SQLite open on startup when nothing changed
    since the last run. Passes the module-level ``read_meta`` so tests patching
    ``...audio_packs.storage.read_meta`` observe the fall-through.
    """
    return _sqlite_index.read_meta_cached(db_path, read_meta)


def lookup(conn: sqlite3.Connection, expression: str, reading: str | None = "") -> list[AudioEntry]:
    """Return AudioEntry list matching expression (and optionally reading).

    reading='' or reading=None both act as wildcards: NULL-reading rows and
    all-reading rows are returned.  Pass a non-empty reading to restrict to
    rows whose reading is NULL or matches exactly.
    """
    expression = unicodedata.normalize("NFC", expression)
    r = unicodedata.normalize("NFC", reading) if reading is not None else ""
    rows = conn.execute(_LOOKUP_SQL, (expression, r, r)).fetchall()
    return [AudioEntry(file=row[0], source=row[1], speaker=row[2], reading=row[3]) for row in rows]
