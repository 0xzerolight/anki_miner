"""SQLite storage layer for indexed dictionaries.

This module owns the schema and all low-level read/write primitives.
Importers populate; providers query.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

SCHEMA_VERSION = 1

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS entries (
    id        INTEGER PRIMARY KEY,
    term      TEXT NOT NULL,
    reading   TEXT,
    content   TEXT NOT NULL,
    score     INTEGER DEFAULT 0,
    sequence  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_term    ON entries(term);
CREATE INDEX IF NOT EXISTS idx_reading ON entries(reading);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

_LOOKUP_SQL = (
    "SELECT content FROM entries "
    "WHERE term = ? OR reading = ? "
    "ORDER BY (term = ?) DESC, sequence "
    "LIMIT 5"
)


@dataclass(frozen=True)
class DictRow:
    """One importable row. Mirrors the entries table schema."""

    term: str
    reading: str | None
    content: str
    score: int = 0
    sequence: int | None = None


def create_index(db_path: Path) -> None:
    """Create a fresh dictionary index at db_path. Idempotent (uses IF NOT EXISTS)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


def bulk_insert(db_path: Path, rows: Iterable[DictRow], batch_size: int = 5000) -> int:
    """Insert rows in batched transactions. Returns total inserted.

    The sqlite3 `with` context manager commits/rolls back but does NOT close
    the connection — we close explicitly so the db file is not held open
    across the importer's staging-dir cleanup (matters on Windows).
    """
    total = 0
    conn = sqlite3.connect(db_path)
    try:
        batch: list[tuple] = []
        for row in rows:
            batch.append((row.term, row.reading, row.content, row.score, row.sequence))
            if len(batch) >= batch_size:
                conn.executemany(
                    "INSERT INTO entries (term, reading, content, score, sequence) "
                    "VALUES (?, ?, ?, ?, ?)",
                    batch,
                )
                total += len(batch)
                batch.clear()
        if batch:
            conn.executemany(
                "INSERT INTO entries (term, reading, content, score, sequence) "
                "VALUES (?, ?, ?, ?, ?)",
                batch,
            )
            total += len(batch)
        conn.commit()
    finally:
        conn.close()
    return total


def write_meta(db_path: Path, items: dict[str, str]) -> None:
    """Upsert meta rows."""
    conn = sqlite3.connect(db_path)
    try:
        for key, value in items.items():
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
        conn.commit()
    finally:
        conn.close()


def read_meta(db_path: Path) -> dict[str, str]:
    """Read all meta rows. Returns empty dict if file missing."""
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(db_path)
    try:
        return {row[0]: row[1] for row in conn.execute("SELECT key, value FROM meta")}
    finally:
        conn.close()


def open_readonly(db_path: Path) -> sqlite3.Connection:
    """Open a read-only connection. Safe to share across threads.

    `check_same_thread=False` is required because providers are constructed
    on the GUI thread (by service_factory) but consumed by worker threads.
    The connection is read-only (`PRAGMA query_only=ON`) so concurrent reads
    are safe under sqlite3's serialized access mode.
    """
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    conn.execute("PRAGMA query_only=ON")
    return conn


def lookup(conn: sqlite3.Connection, word: str) -> list[str]:
    """Return up to 5 content strings matching word (term or reading)."""
    rows = conn.execute(_LOOKUP_SQL, (word, word, word)).fetchall()
    return [row[0] for row in rows]
