"""SQLite storage layer for per-source pitch accent dictionaries.

Mirrors :mod:`anki_miner.services.frequency.storage`: this module owns the
schema and the low-level create/write/read primitives for a single indexed
pitch source living at ``<source_root>/index.sqlite``. The importer populates;
:class:`~anki_miner.services.pitch_accent.provider.IndexedPitchProvider` reads.

Unlike frequency, the ``entries`` table here is a recovery-substrate token, NOT
a query engine: the provider's ``load()`` does one full ``SELECT``, builds the
same in-memory maps the old CSV service built, and closes the connection. The
table exists so the shared store substrate (``validate_index_schema``,
ownership markers, tombstone/backup recovery, startup GC) covers pitch sources
exactly like the other families. Do not clone frequency's per-lookup query
path here — there is nothing to query at runtime.

``nasal``/``devoice`` are stored as the comma-joined digit strings the pitch
CSV format uses (e.g. ``"1,3"``); the provider parses them back to
``tuple[int, ...]`` on load.

Connection idiom: explicit ``try/finally conn.close()`` rather than the sqlite3
``with`` context manager, because ``with`` commits/rolls back but does NOT close
the connection — closing explicitly keeps the db file from being held open
across the importer's staging-dir cleanup (matters on Windows).
"""

from __future__ import annotations

import sqlite3
import unicodedata
from collections.abc import Iterable
from pathlib import Path

import anki_miner.services._sqlite_index as _sqlite_index
from anki_miner.services._sqlite_index import read_meta as read_meta
from anki_miner.services._sqlite_index import write_meta as write_meta

SCHEMA_VERSION = 3

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS entries (
    id      INTEGER PRIMARY KEY,
    reading TEXT NOT NULL,
    kanji   TEXT,
    pattern TEXT NOT NULL,
    nasal   TEXT,
    devoice TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# One row destined for the ``entries`` table, in the pitch CSV column order:
# (reading, kanji, pattern, nasal, devoice). ``pattern`` is the normalized
# integer-downstep / [HL]+ string; nasal/devoice are comma-joined digit strings
# from the CSV format ("" when absent).
PitchStorageRow = tuple[str, str, str, str, str]


def create_index(db_path: Path) -> None:
    """Create a fresh pitch index at ``db_path``. Idempotent (IF NOT EXISTS)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


def bulk_insert(db_path: Path, rows: Iterable[PitchStorageRow], batch_size: int = 5000) -> int:
    """Insert ``(reading, kanji, pattern, nasal, devoice)`` rows in batched transactions.

    Returns the total number inserted. Closes the connection explicitly so the
    db file is not held open across the importer's staging-dir cleanup.
    """
    total = 0
    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        batch: list[PitchStorageRow] = []
        for reading, kanji, pattern, nasal, devoice in rows:
            batch.append(
                (
                    unicodedata.normalize("NFC", reading),
                    unicodedata.normalize("NFC", kanji),
                    pattern,
                    nasal,
                    devoice,
                )
            )
            if len(batch) >= batch_size:
                conn.executemany(
                    "INSERT INTO entries (reading, kanji, pattern, nasal, devoice) VALUES (?, ?, ?, ?, ?)",
                    batch,
                )
                total += len(batch)
                batch.clear()
        if batch:
            conn.executemany(
                "INSERT INTO entries (reading, kanji, pattern, nasal, devoice) VALUES (?, ?, ?, ?, ?)",
                batch,
            )
            total += len(batch)
        conn.commit()
    finally:
        conn.close()
    return total


def build_index(db_path: Path, rows: Iterable[PitchStorageRow], meta: dict[str, str]) -> int:
    """Create the index at ``db_path``, insert ``rows``, then write ``meta``.

    Convenience over ``create_index`` + ``bulk_insert`` + ``write_meta`` so the
    importer has a single call for the happy path. Writes the ``meta.json``
    sidecar via :func:`write_meta`. Returns the inserted entry count.
    """
    create_index(db_path)
    total = bulk_insert(db_path, rows)
    write_meta(db_path, meta)
    return total


def read_meta_cached(db_path: Path) -> dict[str, str]:
    """Read ``meta`` rows via the ``meta.json`` sidecar when it is fresh, falling
    back to :func:`read_meta` when the sidecar is missing/stale/corrupt.

    Lets the pitch registry skip the SQLite open on startup when nothing changed
    since the last run. Thin wrapper over the shared cached reader.
    """
    return _sqlite_index.read_meta_cached(db_path, read_meta)
