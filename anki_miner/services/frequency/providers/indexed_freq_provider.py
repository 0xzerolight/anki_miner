"""SQLite-backed read provider for a single indexed frequency source.

Mirrors :class:`~anki_miner.services.dictionary.providers.indexed_provider.IndexedDictProvider`:
opens the per-source ``index.sqlite`` (built by the frequency source importer)
read-only, validates its ``schema_version``, and exposes term -> rank lookups.

Lookups are reading-scoped (see :func:`_resolve_scoped_rank`): a homograph's rare
reading no longer inherits a common reading's rank. When the caller supplies no
reading, the term-only ``MIN(rank)`` is used (unchanged legacy behavior, and the
compatibility path for reading-less sources).

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
from anki_miner.utils.text_utils import katakana_to_hiragana

logger = logging.getLogger(__name__)


# Ported semantics from Yomitan Translator (ext/js/language/translator.js, the
# ``freq`` case of the term-meta loop, upstream commit e2ed450): a frequency row
# carrying a reading applies only to that reading (``data.reading !== reading``
# → skip); a reading-less (bare) row applies to every reading. We resolve one
# best rank per (term, reading) with a cascade — exact reading first, then bare
# rows, then a term-only fallback so reading-less sources and parser/dict reading
# mismatches still yield a rank rather than losing frequency entirely. Both sides
# are hiragana-normalized so a katakana-stored BCCWJ envelope reading still
# matches a hiragana query.
def _resolve_scoped_rank(rows: list[tuple[str | None, int]], reading: str | None) -> int | None:
    """Best rank for ``reading`` over ``rows`` of ``(stored_reading, rank)``.

    Cascade: exact-reading rows → bare (NULL-reading) rows → all rows (term-only
    MIN). With ``reading`` falsy, only the final term-only MIN applies.
    """
    if not rows:
        return None
    if reading:
        norm = katakana_to_hiragana(reading)
        exact = [rank for stored, rank in rows if stored is not None and katakana_to_hiragana(stored) == norm]
        if exact:
            return min(exact)
        bare = [rank for stored, rank in rows if stored is None]
        if bare:
            return min(bare)
    return min(rank for _stored, rank in rows)


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

    def lookup(self, term: str, reading: str | None = None) -> int | None:
        """Best (minimum) rank for ``term`` scoped to ``reading``, or None.

        With ``reading`` supplied, a homograph's rare reading no longer inherits
        a common reading's rank (see :func:`_resolve_scoped_rank`). With
        ``reading`` None/empty, this is the legacy term-only ``MIN(rank)``.
        """
        if self._conn is None:
            return None
        try:
            rows = self._conn.execute(
                "SELECT reading, rank FROM entries WHERE term = ?",
                (term,),
            ).fetchall()
        except sqlite3.DatabaseError as e:
            logger.warning(
                "Frequency source '%s' (%s) raised DatabaseError during lookup; treating as miss: %s",
                self.source_id,
                self._db_path,
                e,
            )
            return None
        return _resolve_scoped_rank(rows, reading)

    def lookup_many(self, terms: list[str], readings: list[str | None] | None = None) -> dict[str, int | None]:
        """Batch lookup; byte-identical to repeated :meth:`lookup`.

        ``readings`` is an optional parallel list (``readings[i]`` scopes
        ``terms[i]``); when omitted every term is looked up reading-less. One
        IN-clause query gathers the candidate rows, then each requested
        ``(term, reading)`` pair is resolved so the result matches calling
        ``lookup`` once per pair (duplicate terms: last reading wins, exactly as
        a ``{t: lookup(t, r) ...}`` comprehension would).
        """
        if self._conn is None:
            return dict.fromkeys(terms)
        pairs = list(zip(terms, readings if readings is not None else [None] * len(terms), strict=False))
        unique = list(dict.fromkeys(terms))
        if not unique:
            return {}
        placeholders = ",".join("?" * len(unique))
        try:
            rows = self._conn.execute(
                f"SELECT term, reading, rank FROM entries WHERE term IN ({placeholders})",
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
        by_term: dict[str, list[tuple[str | None, int]]] = {}
        for term, reading, rank in rows:
            by_term.setdefault(term, []).append((reading, rank))
        return {t: _resolve_scoped_rank(by_term.get(t, []), r) for t, r in pairs}

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
