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

import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# Sidecar filename living next to each ``index.sqlite``. Holds the pack's
# ``meta`` rows as JSON so the registry can skip the SQLite open on every app
# startup. Refreshed whenever ``write_meta`` runs.
_META_SIDECAR = "meta.json"

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
    "SELECT file, source, speaker FROM entries "
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
    """Result of a lookup query."""

    file: str
    source: str
    speaker: str | None


def create_index(db_path: Path) -> None:
    """Create a fresh audio pack index at db_path. Idempotent (uses IF NOT EXISTS)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


def bulk_insert(db_path: Path, rows: Iterable[AudioPackRow], batch_size: int = 5000) -> int:
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
            batch.append(
                (
                    row.expression,
                    row.reading,
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
                "INSERT INTO entries (expression, reading, source, speaker, display, file) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                batch,
            )
            total += len(batch)
        # All batches accumulate in one transaction; commit is atomic at end — no partial durability.
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

    Used by the registry to skip the SQLite open on startup when nothing
    changed since the last run.
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

    ``check_same_thread=False`` is required because fetchers may be constructed
    on the GUI thread but consumed by worker threads. The connection is
    read-only (``PRAGMA query_only=ON``) so concurrent reads are safe under
    sqlite3's serialized access mode.
    """
    # Build the file: URI via Path.as_uri() so URI-significant characters in
    # the path (``#`` fragment, ``?`` query, ``%`` escape) are percent-encoded.
    # A raw f-string would let a pack_dir containing any of these truncate the
    # path. as_uri() needs an absolute path, so resolve first.
    uri = db_path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    conn.execute("PRAGMA query_only=ON")
    return conn


def lookup(conn: sqlite3.Connection, expression: str, reading: str | None = "") -> list[AudioEntry]:
    """Return AudioEntry list matching expression (and optionally reading).

    reading='' or reading=None both act as wildcards: NULL-reading rows and
    all-reading rows are returned.  Pass a non-empty reading to restrict to
    rows whose reading is NULL or matches exactly.
    """
    r = reading if reading is not None else ""
    rows = conn.execute(_LOOKUP_SQL, (expression, r, r)).fetchall()
    return [AudioEntry(file=row[0], source=row[1], speaker=row[2]) for row in rows]
