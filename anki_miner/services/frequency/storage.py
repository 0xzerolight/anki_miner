"""SQLite storage layer for per-source frequency dictionaries.

Mirrors :mod:`anki_miner.services.dictionary.storage`: this module owns the
schema and the low-level create/write/read primitives for a single indexed
frequency source living at ``<source_root>/index.sqlite``. The importer
populates; a later provider task queries.

Each source is a small table of ``(term, reading, rank)`` rows plus a ``meta``
key/value table. A ``meta.json`` sidecar next to ``index.sqlite`` lets a
registry read the metadata on startup without opening SQLite (same idiom as the
dictionary storage layer).

Connection idiom: explicit ``try/finally conn.close()`` rather than the sqlite3
``with`` context manager, because ``with`` commits/rolls back but does NOT close
the connection — closing explicitly keeps the db file from being held open
across the importer's staging-dir cleanup (matters on Windows).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

import anki_miner.services._sqlite_index as _sqlite_index
from anki_miner.services._sqlite_index import read_meta as read_meta
from anki_miner.services._sqlite_index import write_meta as write_meta

# v2 (this release) adds a nullable ``display_value`` column. The change is
# purely additive — term/reading/rank are unchanged — so a v1 index (which lacks
# the column) is still fully readable: readers treat display_value as absent (see
# IndexedFreqProvider.load / FrequencySourceRegistry). No reimport is ever
# required for correctness; a reimport only *gains* display values. Any migration
# past v2 must stay additive to keep this backward-read guarantee.
SCHEMA_VERSION = 2

# Sentinel rank stored for a word-based (categorical) source's rows. Its real
# level lives in ``display_value``; the rank column only holds this out-of-band
# value so the row is a no-op in numeric aggregation. Chosen large (2**31 - 1 —
# well beyond any real frequency rank) so the aggregation helpers exclude it via
# ``rank < CATEGORICAL_RANK``, and so a consumer that forgets the filter fails
# safe (the word looks *rarest*, filtered out, never falsely "common"). No
# schema change: it is an ordinary INTEGER value in the existing ``rank`` column.
CATEGORICAL_RANK = 2**31 - 1

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS entries (
    id            INTEGER PRIMARY KEY,
    term          TEXT NOT NULL,
    reading       TEXT,
    rank          INTEGER NOT NULL,
    display_value TEXT
);
CREATE INDEX IF NOT EXISTS idx_term ON entries(term);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# One row destined for the ``entries`` table: (term, reading, rank, display_value).
# ``display_value`` is the human string Yomitan preserves for string/displayValue
# payloads (e.g. "1099/72000", JPDB ㋕ markers); None for plain-int/CSV ranks.
FreqRow = tuple[str, str | None, int, str | None]


def create_index(db_path: Path) -> None:
    """Create a fresh frequency index at ``db_path``. Idempotent (IF NOT EXISTS)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


def bulk_insert(db_path: Path, rows: Iterable[FreqRow], batch_size: int = 5000) -> int:
    """Insert ``(term, reading, rank, display_value)`` rows in batched transactions.

    Returns the total number inserted. Closes the connection explicitly so the
    db file is not held open across the importer's staging-dir cleanup.
    """
    total = 0
    conn = sqlite3.connect(db_path)
    try:
        batch: list[FreqRow] = []
        for row in rows:
            batch.append(row)
            if len(batch) >= batch_size:
                conn.executemany(
                    "INSERT INTO entries (term, reading, rank, display_value) VALUES (?, ?, ?, ?)",
                    batch,
                )
                total += len(batch)
                batch.clear()
        if batch:
            conn.executemany(
                "INSERT INTO entries (term, reading, rank, display_value) VALUES (?, ?, ?, ?)",
                batch,
            )
            total += len(batch)
        conn.commit()
    finally:
        conn.close()
    return total


def build_index(db_path: Path, rows: Iterable[FreqRow], meta: dict[str, str]) -> int:
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

    Lets a frequency registry skip the SQLite open on startup when nothing
    changed since the last run. Thin wrapper over the shared cached reader.
    """
    return _sqlite_index.read_meta_cached(db_path, read_meta)
