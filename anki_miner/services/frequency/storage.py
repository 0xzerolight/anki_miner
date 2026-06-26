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

import json
import logging
import sqlite3
from collections.abc import Iterable
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# Sidecar filename living next to each ``index.sqlite``. Holds the source's
# ``meta`` rows as JSON so a registry ``load()`` can skip the SQLite open on
# every app startup. Refreshed whenever ``write_meta`` runs.
_META_SIDECAR = "meta.json"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS entries (
    id      INTEGER PRIMARY KEY,
    term    TEXT NOT NULL,
    reading TEXT,
    rank    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_term ON entries(term);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# One row destined for the ``entries`` table.
FreqRow = tuple[str, str | None, int]


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
    """Insert ``(term, reading, rank)`` rows in batched transactions.

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
                    "INSERT INTO entries (term, reading, rank) VALUES (?, ?, ?)",
                    batch,
                )
                total += len(batch)
                batch.clear()
        if batch:
            conn.executemany(
                "INSERT INTO entries (term, reading, rank) VALUES (?, ?, ?)",
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


def write_meta(db_path: Path, items: dict[str, str]) -> None:
    """Upsert ``meta`` rows and refresh the ``meta.json`` sidecar.

    The sidecar lets the next :func:`read_meta_cached` call avoid re-opening
    SQLite when nothing changed.
    """
    conn = sqlite3.connect(db_path)
    try:
        for key, value in items.items():
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
        conn.commit()
        full_meta = {row[0]: row[1] for row in conn.execute("SELECT key, value FROM meta")}
    finally:
        conn.close()
    _write_meta_sidecar(db_path, full_meta)


def read_meta(db_path: Path) -> dict[str, str]:
    """Read all ``meta`` rows. Returns an empty dict if the file is missing."""
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(db_path)
    try:
        return {row[0]: row[1] for row in conn.execute("SELECT key, value FROM meta")}
    finally:
        conn.close()


def read_meta_cached(db_path: Path) -> dict[str, str]:
    """Read ``meta`` rows via the ``meta.json`` sidecar when it is fresh.

    Falls through to :func:`read_meta` and rewrites the sidecar when:
    * the sidecar is missing,
    * ``index.sqlite`` is newer than the sidecar,
    * the sidecar is unreadable / not valid JSON.

    Mirrors the dictionary storage layer so a frequency registry can skip the
    SQLite open on startup when nothing changed since the last run.
    """
    if not db_path.exists():
        return {}
    sidecar = db_path.parent / _META_SIDECAR
    try:
        if sidecar.is_file() and sidecar.stat().st_mtime >= db_path.stat().st_mtime:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
    except (OSError, json.JSONDecodeError) as e:
        logger.debug("meta sidecar miss for %s: %s", db_path, e)

    meta = read_meta(db_path)
    _write_meta_sidecar(db_path, meta)
    return meta


def _write_meta_sidecar(db_path: Path, meta: dict[str, str]) -> None:
    """Best-effort sidecar write. Cache misses are logged, not raised — the
    next :func:`read_meta_cached` call simply falls back to :func:`read_meta`."""
    sidecar = db_path.parent / _META_SIDECAR
    try:
        sidecar.write_text(json.dumps(meta), encoding="utf-8")
    except OSError as e:  # pragma: no cover - defensive
        logger.debug("Failed to write meta sidecar %s: %s", sidecar, e)
