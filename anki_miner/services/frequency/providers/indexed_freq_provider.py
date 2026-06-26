"""SQLite-backed read provider for a single indexed frequency source.

Mirrors :class:`~anki_miner.services.dictionary.providers.indexed_provider.IndexedDictProvider`:
opens the per-source ``index.sqlite`` (built by the frequency source importer)
read-only, validates its ``schema_version``, and exposes term -> rank lookups.

The lookup key is the ``term`` column alone; the ``reading`` column is stored
for display/import provenance but never used to match. When a term has multiple
homograph rows (different readings, different ranks), ``MIN(rank)`` wins — the
best (most frequent) reading represents the surface form.

Threading: the read-only connection is opened with ``check_same_thread=False``
so one provider instance is safe to share across threads (constructed on the GUI
thread, consumed by worker threads), the same contract as IndexedDictProvider.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
from pathlib import Path

from anki_miner.services.frequency.storage import SCHEMA_VERSION, read_meta_cached

logger = logging.getLogger(__name__)


class IndexedFreqProvider:
    """SQLite-backed read provider for one frequency source.

    Construct, then call :meth:`load` before any lookup. ``load`` returns False
    (never raises) on a missing file or schema mismatch so the registry can drop
    a bad source without aborting the chain.
    """

    def __init__(self, source_id: str, db_path: Path, display_name: str):
        self.source_id = source_id
        self._db_path = db_path
        self._display_name = display_name
        self._conn: sqlite3.Connection | None = None

    @property
    def name(self) -> str:
        return self._display_name

    def is_available(self) -> bool:
        return self._conn is not None

    def load(self) -> bool:
        if self._conn is not None:
            return True
        if not self._db_path.exists():
            logger.warning("Frequency index missing: %s", self._db_path)
            return False

        try:
            meta = read_meta_cached(self._db_path)
        except sqlite3.DatabaseError as e:
            logger.warning("Frequency index unreadable (%s): %s", self._db_path, e)
            return False

        try:
            version = int(meta.get("schema_version", "0"))
        except ValueError:
            version = 0
        if version != SCHEMA_VERSION:
            logger.warning(
                "Frequency source %s has schema_version=%s, expected %s — needs reimport",
                self.source_id,
                version,
                SCHEMA_VERSION,
            )
            return False

        try:
            self._conn = self._open_readonly(self._db_path)
        except sqlite3.DatabaseError as e:
            logger.warning("Failed to open %s: %s", self._db_path, e)
            return False
        return True

    def lookup(self, term: str) -> int | None:
        """Best (minimum) rank for ``term``, or None if not found / not loaded."""
        if self._conn is None:
            return None
        try:
            row = self._conn.execute(
                "SELECT MIN(rank) FROM entries WHERE term = ?",
                (term,),
            ).fetchone()
        except sqlite3.DatabaseError as e:
            logger.warning(
                "Frequency source '%s' (%s) raised DatabaseError during lookup; treating as miss: %s",
                self.source_id,
                self._db_path,
                e,
            )
            return None
        if row is None or row[0] is None:
            return None
        return int(row[0])

    def lookup_many(self, terms: list[str]) -> dict[str, int | None]:
        """Batch lookup; byte-identical to repeated :meth:`lookup`.

        One IN-clause query gathers the per-term minimum rank, then every
        requested term (including duplicates and misses) is re-expanded so the
        result matches calling ``lookup`` once per term.
        """
        if self._conn is None:
            return dict.fromkeys(terms)
        unique = list(dict.fromkeys(terms))
        if not unique:
            return {}
        placeholders = ",".join("?" * len(unique))
        try:
            rows = self._conn.execute(
                f"SELECT term, MIN(rank) FROM entries WHERE term IN ({placeholders}) GROUP BY term",
                unique,
            ).fetchall()
        except sqlite3.DatabaseError as e:
            logger.warning(
                "Frequency source '%s' (%s) raised DatabaseError during lookup_many; treating as all-miss: %s",
                self.source_id,
                self._db_path,
                e,
            )
            return dict.fromkeys(terms)
        by_term: dict[str, int | None] = {term: (int(rank) if rank is not None else None) for term, rank in rows}
        return {t: by_term.get(t) for t in terms}

    @staticmethod
    def _open_readonly(db_path: Path) -> sqlite3.Connection:
        """Open a read-only, thread-shareable connection.

        Uses a ``file:...?mode=ro`` URI built via ``Path.as_uri()`` so
        URI-significant characters in the path (``#``/``?``/``%``) are
        percent-encoded, mirroring the dictionary storage layer.
        """
        uri = db_path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        conn.execute("PRAGMA query_only=ON")
        return conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self.close()
