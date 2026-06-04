"""SQLite storage layer for indexed dictionaries.

This module owns the schema and all low-level read/write primitives.
Importers populate; providers query.

Note on connection idiom: This module deliberately uses explicit ``try/finally
conn.close()`` rather than ``with sqlite3.connect()`` as a context manager.
Reason: the sqlite3 ``with`` block commits/rolls back but does NOT close the
connection — we close explicitly so the db file is not held open across the
importer's staging-dir cleanup (matters on Windows where open file handles
block directory deletion).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2

# Sidecar filename living next to each ``index.sqlite``. Holds the dictionary's
# ``meta`` rows as JSON so ``DictionaryRegistry.load()`` can skip the SQLite
# open on every app startup. Refreshed whenever ``write_meta`` runs.
_META_SIDECAR = "meta.json"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS entries (
    id        INTEGER PRIMARY KEY,
    term      TEXT NOT NULL,
    reading   TEXT,
    content   TEXT NOT NULL,
    tags      TEXT NOT NULL DEFAULT '',
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
    "SELECT content, tags FROM entries "
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
    tags: str = ""
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
            batch.append((row.term, row.reading, row.content, row.tags, row.score, row.sequence))
            if len(batch) >= batch_size:
                conn.executemany(
                    "INSERT INTO entries (term, reading, content, tags, score, sequence) " "VALUES (?, ?, ?, ?, ?, ?)",
                    batch,
                )
                total += len(batch)
                batch.clear()
        if batch:
            conn.executemany(
                "INSERT INTO entries (term, reading, content, tags, score, sequence) " "VALUES (?, ?, ?, ?, ?, ?)",
                batch,
            )
            total += len(batch)
        conn.commit()
    finally:
        conn.close()
    return total


def write_meta(db_path: Path, items: dict[str, str]) -> None:
    """Upsert meta rows. Refreshes the ``meta.json`` sidecar so the next
    ``read_meta_cached`` call avoids re-opening SQLite."""
    conn = sqlite3.connect(db_path)
    try:
        for key, value in items.items():
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) " "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
        conn.commit()
        full_meta = {row[0]: row[1] for row in conn.execute("SELECT key, value FROM meta")}
    finally:
        conn.close()
    _write_meta_sidecar(db_path, full_meta)


def read_meta(db_path: Path) -> dict[str, str]:
    """Read all meta rows. Returns empty dict if file missing."""
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(db_path)
    try:
        return {row[0]: row[1] for row in conn.execute("SELECT key, value FROM meta")}
    finally:
        conn.close()


def read_meta_cached(db_path: Path) -> dict[str, str]:
    """Read meta rows via the ``meta.json`` sidecar when fresh.

    Falls through to ``read_meta`` and rewrites the sidecar when:
    * the sidecar is missing,
    * ``index.sqlite`` is newer than the sidecar,
    * the sidecar is unreadable / not valid JSON.

    Used by ``DictionaryRegistry.load()`` to skip the SQLite open on startup
    when nothing changed since the last run.
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
    next ``read_meta_cached`` call will simply fall back to ``read_meta``."""
    sidecar = db_path.parent / _META_SIDECAR
    try:
        sidecar.write_text(json.dumps(meta), encoding="utf-8")
    except OSError as e:  # pragma: no cover - defensive
        logger.debug("Failed to write meta sidecar %s: %s", sidecar, e)


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


def lookup(conn: sqlite3.Connection, word: str) -> list[tuple[str, str]]:
    """Return up to 5 (content, tags) pairs matching word (term or reading)."""
    rows = conn.execute(_LOOKUP_SQL, (word, word, word)).fetchall()
    return [(row[0], row[1]) for row in rows]


# sqlite's default SQLITE_MAX_VARIABLE_NUMBER is 999. lookup_many binds each
# word twice (term IN + reading IN), so a single chunk may use at most
# 2 * _BIND_CHUNK variables. Keep the product comfortably under the cap.
_BIND_CHUNK = 450


def lookup_many(conn: sqlite3.Connection, words: list[str]) -> dict[str, list[tuple[str, str]]]:
    """Batch variant of :func:`lookup`.

    Runs ONE query per chunk (``WHERE term IN (...) OR reading IN (...)``)
    instead of one query per word, then reproduces ``_LOOKUP_SQL``'s ordering
    and ``LIMIT 5`` in Python so each per-word result is byte-identical,
    row-for-row, to ``lookup(conn, word)``.

    Returns a dict keyed by every requested word (duplicates collapse). A word
    with no matches maps to ``[]``, mirroring ``lookup``'s empty-result case.
    """
    # Preserve first-seen order; collapse duplicate requests to one bucket.
    unique: list[str] = []
    seen: set[str] = set()
    for w in words:
        if w not in seen:
            seen.add(w)
            unique.append(w)

    result: dict[str, list[tuple[str, str]]] = {w: [] for w in unique}
    if not unique:
        return result

    for start in range(0, len(unique), _BIND_CHUNK):
        chunk = unique[start : start + _BIND_CHUNK]
        placeholders = ", ".join("?" for _ in chunk)
        sql = (
            "SELECT id, term, reading, content, tags, sequence FROM entries "
            f"WHERE term IN ({placeholders}) OR reading IN ({placeholders})"
        )
        rows = conn.execute(sql, (*chunk, *chunk)).fetchall()

        # Bucket each fetched row to every requested word it can satisfy. A row
        # may match one word by term and a different word by reading. Each entry
        # carries the sort keys that reproduce _LOOKUP_SQL's
        # "ORDER BY (term=?) DESC, sequence", plus a final ``id`` tiebreak:
        #   * term_priority: 0 when this row's term equals the word (DESC puts
        #     term matches first), else 1.
        #   * _seq_key(sequence): NULL-aware ascending sequence tiebreak.
        #   * row_id: SQLite resolves equal (term_priority, sequence) ties by
        #     rowid ascending under the single-word query's MULTI-INDEX OR plan;
        #     replaying it here keeps lookup_many byte-identical to lookup.
        chunk_set = set(chunk)
        buckets: dict[str, list[tuple[int, tuple[int, int], int, str, str]]] = {w: [] for w in chunk}
        for row_id, term, reading, content, tags, sequence in rows:
            tags_val = tags if tags is not None else ""
            seq_key = _seq_key(sequence)
            # A row satisfies a word via term OR reading. _LOOKUP_SQL's
            # ``term=? OR reading=?`` returns each row ONCE per word even when
            # both columns match, so de-dup the (term, reading) pair here.
            for w in {term, reading}:
                if w is not None and w in chunk_set:
                    term_priority = 0 if term == w else 1
                    buckets[w].append((term_priority, seq_key, row_id, content, tags_val))

        for w, entries in buckets.items():
            entries.sort(key=lambda e: (e[0], e[1], e[2]))
            result[w] = [(content, tags) for _p, _s, _id, content, tags in entries[:5]]

    return result


# Sort key mirroring SQLite "ORDER BY sequence": NULL sorts before any value.
# (is_not_null, value) where NULL -> (0, 0) sorts ahead of any integer.
def _seq_key(sequence: int | None) -> tuple[int, int]:
    if sequence is None:
        return (0, 0)
    return (1, sequence)
