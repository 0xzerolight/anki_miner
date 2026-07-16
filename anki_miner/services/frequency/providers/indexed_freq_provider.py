"""SQLite-backed read provider for a single indexed frequency source.

Mirrors :class:`~anki_miner.services.dictionary.providers.indexed_provider.IndexedDictProvider`:
opens the per-source ``index.sqlite`` (built by the frequency source importer)
read-only, validates its ``schema_version``, and exposes term -> rank lookups.

Lookups are reading-scoped (see :func:`_select_scoped_row`): a homograph's rare
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

from anki_miner.services._sqlite_index import open_readonly
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
def _select_scoped_row(rows: list[tuple], reading: str | None) -> tuple | None:
    """Return the cascade's winning row (min rank in the selected bucket), or None.

    Each row is ``(stored_reading, rank, ...)`` — ``row[0]`` is the reading,
    ``row[1]`` the rank; any trailing columns (e.g. ``display_value``) ride along
    untouched. Cascade: exact-reading rows → bare (NULL-reading) rows → all rows
    (term-only MIN). With ``reading`` falsy, only the final term-only MIN applies.
    """
    if not rows:
        return None
    if reading:
        norm = katakana_to_hiragana(reading)
        exact = [r for r in rows if r[0] is not None and katakana_to_hiragana(r[0]) == norm]
        if exact:
            return min(exact, key=lambda r: r[1])
        bare = [r for r in rows if r[0] is None]
        if bare:
            return min(bare, key=lambda r: r[1])
    return min(rows, key=lambda r: r[1])


class IndexedFreqProvider:
    """SQLite-backed read provider for one frequency source.

    Construct, then call :meth:`load` before any lookup. ``load`` returns False
    (never raises) on a missing file or schema mismatch so the registry can drop
    a bad source without aborting the chain.
    """

    def __init__(self, source_id: str, db_path: Path, display_name: str, is_categorical: bool = False):
        self.source_id = source_id
        self._db_path = db_path
        self._display_name = display_name
        # Word-based (categorical) source: its rows carry the CATEGORICAL_RANK
        # sentinel, so it never contributes a numeric rank. Threaded from
        # FreqSourceMeta so MultiFrequencyService.has_numeric_source can keep the
        # max_frequency_rank cutoff inert on a categorical-only chain.
        self.is_categorical = is_categorical
        self._conn: sqlite3.Connection | None = None
        # Set at load() from PRAGMA table_info: a v1 index predates the
        # display_value column, so its detail lookups report display None.
        self._has_display_value = False

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
        # Backward-compatible read: any version from 1..SCHEMA_VERSION loads
        # (additive-only migrations). A v1 index simply lacks display_value.
        # A future version > SCHEMA_VERSION is rejected — unknown schema.
        if not (1 <= version <= SCHEMA_VERSION):
            logger.warning(
                "Frequency source %s has schema_version=%s, supported range is 1..%s — needs reimport",
                self.source_id,
                version,
                SCHEMA_VERSION,
            )
            return False

        try:
            self._conn = open_readonly(self._db_path)
        except sqlite3.DatabaseError as e:
            logger.warning("Failed to open %s: %s", self._db_path, e)
            return False
        self._has_display_value = self._detect_display_value(self._conn)
        return True

    @staticmethod
    def _detect_display_value(conn: sqlite3.Connection) -> bool:
        """True when the ``entries`` table carries the v2 ``display_value`` column.

        Read straight from ``PRAGMA table_info`` rather than trusting the version
        number, so a physical schema mismatch can never mis-shape the SELECT.
        """
        cols = {row[1] for row in conn.execute("PRAGMA table_info(entries)")}
        return "display_value" in cols

    def lookup(self, term: str, reading: str | None = None) -> int | None:
        """Best (minimum) rank for ``term`` scoped to ``reading``, or None.

        With ``reading`` supplied, a homograph's rare reading no longer inherits
        a common reading's rank (see :func:`_select_scoped_row`). With ``reading``
        None/empty, this is the legacy term-only ``MIN(rank)``.
        """
        detail = self.lookup_detail(term, reading)
        return detail[0] if detail is not None else None

    def lookup_detail(self, term: str, reading: str | None = None) -> tuple[int, str | None] | None:
        """``(rank, display_value)`` for the reading-scoped winning row, or None.

        The display value is the human string of the winning row (None on a v1
        index that predates the column, or on plain-int/CSV ranks). Feeds the
        card's per-source breakdown; the filter/sort paths take the rank alone
        via :meth:`lookup`.
        """
        if self._conn is None:
            return None
        display_col = "display_value" if self._has_display_value else "NULL"
        try:
            rows = self._conn.execute(
                f"SELECT reading, rank, {display_col} FROM entries WHERE term = ?",
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
        row = _select_scoped_row(rows, reading)
        if row is None:
            return None
        return int(row[1]), row[2]

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self.close()
