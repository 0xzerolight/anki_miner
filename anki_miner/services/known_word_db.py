"""Service for managing a local SQLite database of known words."""

import logging
import os
import sqlite3
import unicodedata
from contextlib import closing
from pathlib import Path

from anki_miner.utils.logging_ext import log_summary

logger = logging.getLogger(__name__)

#: Schema revision stored in ``PRAGMA user_version``. Bumped when a migration
#: must run once per database rather than on every ``initialize()``.
_SCHEMA_VERSION = 1


def normalize_lemma(word: str) -> str:
    """Return the canonical storage form of a known-word lemma.

    Canonically equivalent Japanese strings must share one lexical identity:
    ``lemma`` is the PRIMARY KEY and the mining filter is exact set membership,
    so an NFD row imported from a word list would never match the NFC form the
    tokenizer produces, and the user would keep getting cards for a word they
    marked known. NFC is the same normal form the Anki first-field duplicate
    check already uses (``anki_note_builder``), so both sides agree.
    """
    return unicodedata.normalize("NFC", word)


def _normalize_all(words: set[str]) -> set[str]:
    """Normalize a lemma set, collapsing canonical duplicates."""
    return {normalize_lemma(word) for word in words}


class KnownWordDB:
    """Persistent local database of known words.

    Caches known vocabulary in a SQLite database so that AnkiConnect
    does not need to be queried for the full word list on every run.
    Supports differential sync: words are only added, never removed.
    """

    def __init__(self, db_path: Path):
        """Initialize the known word database.

        Args:
            db_path: Path to the SQLite database file.
        """
        self._db_path = db_path
        # Run-lifetime memo (T24): the batch worker keeps one processor — and
        # one KnownWordDB — alive for every queue item (T20), so a full-table
        # scan + NFC-normalize on every get_known_words()/get_words_by_source()
        # call was pure repeat work within a run. Invalidated by every writer
        # below. It does NOT see a write from another process sharing this DB
        # file (Issue #100 double launch); that staleness window already
        # existed on the underlying reads and matches what the other
        # run-cached queue services tolerate.
        self._known_cache: set[str] | None = None
        self._source_cache: dict[str, set[str]] = {}
        # Anki-vocabulary normalization memo for sync_with_anki: AnkiService
        # caches get_existing_vocabulary() for the run too, so the same set
        # object is passed here on every queue item. Keyed by identity against
        # a retained strong reference (never a bare id() — an id can be
        # reused by an unrelated object once the original is garbage
        # collected, which would silently serve the wrong normalized set).
        self._anki_vocab_ref: set[str] | None = None
        self._anki_vocab_normalized: set[str] | None = None

    def _invalidate_cache(self) -> None:
        """Drop the run-cached known/source sets after a write."""
        self._known_cache = None
        self._source_cache = {}

    def _connect(self) -> sqlite3.Connection:
        """Open a connection with a 5 s busy timeout.

        Two app processes can share this DB (Issue #100 double launch); with
        the default rollback journal and no timeout, a concurrent writer made
        every collision an instant "database is locked". Mirrors
        ``StatsService._connect``; journal mode deliberately unchanged.
        """
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def initialize(self) -> None:
        """Create the database and schema if they don't exist.

        Creates the parent directories and the known_words table.
        """
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS known_words ("
                "lemma TEXT PRIMARY KEY, "
                "source TEXT DEFAULT 'anki', "
                "added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
                ")"
            )
            conn.commit()
            self._migrate_to_nfc(conn)

    def _migrate_to_nfc(self, conn: sqlite3.Connection) -> None:
        """Rewrite pre-normalization rows to NFC, once per database.

        Rows written before ``normalize_lemma`` existed can hold NFD spellings
        that no lookup will ever match. Two rows can also normalize onto the
        same lemma; those merge, keeping ``source='user'`` if any side had it so
        a curated "mark known" is never downgraded, and the earliest
        ``added_at``. Gated on ``PRAGMA user_version`` so a large collection is
        not rescanned on every launch.
        """
        try:
            conn.execute("BEGIN IMMEDIATE")
            if int(conn.execute("PRAGMA user_version").fetchone()[0]) >= _SCHEMA_VERSION:
                conn.commit()
                return
            rows = conn.execute("SELECT lemma, source, added_at FROM known_words").fetchall()
            merged: dict[str, tuple[str, str]] = {}
            rewritten = False
            for lemma, source, added_at in rows:
                canonical = normalize_lemma(lemma)
                if canonical != lemma:
                    rewritten = True
                previous = merged.get(canonical)
                if previous is None:
                    merged[canonical] = (source, added_at)
                    continue
                rewritten = True
                previous_source, previous_added_at = previous
                merged[canonical] = (
                    "user" if "user" in (previous_source, source) else previous_source,
                    min(previous_added_at, added_at),
                )
            if rewritten:
                conn.execute("DELETE FROM known_words")
                conn.executemany(
                    "INSERT INTO known_words (lemma, source, added_at) VALUES (?, ?, ?)",
                    [(lemma, source, added_at) for lemma, (source, added_at) in merged.items()],
                )
            conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
            raise

    def is_available(self) -> bool:
        """Check if the database file exists and is readable.

        Returns:
            True if the database is ready for use.
        """
        exists = self._db_path.exists()
        readable = exists and os.access(self._db_path, os.R_OK)
        if exists and not readable:
            logger.warning(
                "Known words unavailable: file=%s reason=unreadable",
                self._db_path.name,
            )
        return readable

    def get_known_words(self) -> set[str]:
        """Return all known word lemmas, NFC-normalized.

        Normalizing on read as well as on write is what makes the guarantee
        unconditional. :meth:`_migrate_to_nfc` only runs from
        :meth:`initialize`, and ``service_factory`` calls that only when
        ``config.use_known_words_db`` is on — while the user ignore list is read
        on EVERY run regardless (Issue #42). A pre-fix NFD row in a database
        that never got initialized would otherwise still miss the NFC probe and
        re-card a word the user marked known. Idempotent and cheap.

        Returns:
            Set of all lemma strings in the database.
        """
        if self._known_cache is not None:
            return self._known_cache
        with closing(self._connect()) as conn:
            cursor = conn.execute("SELECT lemma FROM known_words")
            words = _normalize_all({row[0] for row in cursor.fetchall()})
        log_summary(logger, "Known words load done", rows=len(words))
        self._known_cache = words
        return words

    def get_words_by_source(self, source: str) -> set[str]:
        """Return all lemmas stored under a given source label, NFC-normalized.

        Used for the user-curated ignore list (Issue #42): ``source='user'``
        words are applied on every mining run regardless of
        ``config.use_known_words_db`` — which is precisely the path the
        ``initialize()``-gated migration does not cover, hence the read-side
        fold (see :meth:`get_known_words`).

        Args:
            source: Source label to filter on (e.g. 'anki', 'user').

        Returns:
            Set of lemma strings with the matching source.
        """
        if source in self._source_cache:
            return self._source_cache[source]
        with closing(self._connect()) as conn:
            cursor = conn.execute("SELECT lemma FROM known_words WHERE source = ?", (source,))
            words = _normalize_all({row[0] for row in cursor.fetchall()})
        log_summary(
            logger,
            "Known words source load done",
            source=source,
            rows=len(words),
        )
        self._source_cache[source] = words
        return words

    def add_words(self, words: set[str], source: str = "anki") -> int:
        """Bulk insert words into the database, ignoring duplicates.

        Args:
            words: Set of lemma strings to add.
            source: Source label (e.g. 'anki', 'mined', 'user').

        Returns:
            Number of newly inserted rows (an in-place source upgrade is not
            counted as new).
        """
        words = _normalize_all(words)
        if not words:
            return 0

        # ``lemma`` is the PRIMARY KEY, so a plain INSERT OR IGNORE no-ops when
        # the row already exists. That silently dropped a user "mark known" when
        # the lemma was already cached as source='anki': the mark never took, and
        # clear(preserve_user=True) on Rebuild then deleted the anki row — losing
        # the user's entry and violating the Issue #42 "user list survives
        # rebuild" invariant (T-27).
        #
        # When marking as 'user' we therefore UPGRADE an existing row's source on
        # conflict. For every other source (anki/mined) we keep IGNORE so a later
        # sync can never DOWNGRADE a 'user' row back to 'anki'.
        with closing(self._connect()) as conn:
            before = self._count(conn)
            if source == "user":
                conn.executemany(
                    "INSERT INTO known_words (lemma, source) VALUES (?, ?) "
                    "ON CONFLICT(lemma) DO UPDATE SET source=excluded.source",
                    [(w, source) for w in words],
                )
            else:
                conn.executemany(
                    "INSERT OR IGNORE INTO known_words (lemma, source) VALUES (?, ?)",
                    [(w, source) for w in words],
                )
            conn.commit()
            after = self._count(conn)
            self._invalidate_cache()
            return after - before

    def add_words_with_receipt(self, words: set[str], source: str = "anki") -> set[str]:
        """Bulk insert words and return the exact newly inserted lemmas.

        The receipt is derived from each INSERT result inside the same
        transaction, so callers never need a racy before/after snapshot.
        Existing rows upgraded to ``source='user'`` are not newly inserted.
        """
        words = _normalize_all(words)
        if not words:
            return set()

        with closing(self._connect()) as conn:
            inserted: set[str] = set()
            for word in sorted(words):
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO known_words (lemma, source) VALUES (?, ?)",
                    (word, source),
                )
                if cursor.rowcount == 1:
                    inserted.add(word)
                elif source == "user":
                    conn.execute("UPDATE known_words SET source = ? WHERE lemma = ?", (source, word))
            conn.commit()
            self._invalidate_cache()
            return inserted

    def sync_with_anki(
        self,
        anki_vocabulary: set[str],
        existing: set[str] | None = None,
    ) -> tuple[int, int]:
        """Differential sync: add words from Anki that are not yet in the DB.

        Words that are in the DB but no longer in Anki are NOT removed
        (the user may have deleted a card but still knows the word).

        Args:
            anki_vocabulary: Current set of vocabulary from AnkiConnect.
            existing: Pre-fetched current known set. When supplied, skips the
                internal full-table scan — callers that already hold the set
                (e.g. episode_processor filtering before sync) should pass it.

        Returns:
            Tuple of (newly_added_count, total_count).
        """
        if existing is None:
            existing = self.get_known_words()
        # ``existing`` is normally this object's own known-set memo (the
        # get_known_words() return value), which is already NFC-normalized —
        # re-normalizing it here was pure repeat work on top of the memo.
        # A caller-supplied set that is NOT this memo is normalized as before.
        normalized_existing = existing if existing is self._known_cache else _normalize_all(existing)
        normalized_anki = self._normalize_anki_vocabulary(anki_vocabulary)
        new_words = normalized_anki - normalized_existing
        added = self.add_words(new_words, source="anki")
        return (added, len(existing) + added)

    def _normalize_anki_vocabulary(self, anki_vocabulary: set[str]) -> set[str]:
        """Return the NFC-normalized Anki vocabulary, memoized by identity.

        See the memo fields in ``__init__`` for why identity (with a retained
        strong reference) is the correct cache key here.
        """
        if anki_vocabulary is self._anki_vocab_ref:
            assert self._anki_vocab_normalized is not None
            return self._anki_vocab_normalized
        normalized = _normalize_all(anki_vocabulary)
        self._anki_vocab_ref = anki_vocabulary
        self._anki_vocab_normalized = normalized
        return normalized

    def remove_words(self, words: set[str], source: str | None = None) -> int:
        """Delete specific words from the database (Issue #42).

        Used by the Manage Known Words dialog to remove individual user-added
        entries, and by the Undo callback to revert ``source='mined'`` rows
        without touching ``source='user'`` or ``source='anki'`` rows (OVH-030).

        Args:
            words: Set of lemma strings to remove.
            source: When given, only rows whose ``source`` matches this value
                are removed.  When ``None`` (default), all rows matching the
                lemma are removed regardless of source.  Pass ``source='mined'``
                from the Undo path to scope removal to the session's newly mined
                rows and leave user-curated (Issue #42) and Anki-synced rows
                untouched.

        Returns:
            Number of rows actually removed.
        """
        words = _normalize_all(words)
        if not words:
            return 0

        with closing(self._connect()) as conn:
            before = self._count(conn)
            if source is None:
                conn.executemany("DELETE FROM known_words WHERE lemma = ?", [(w,) for w in words])
            else:
                conn.executemany(
                    "DELETE FROM known_words WHERE lemma = ? AND source = ?",
                    [(w, source) for w in words],
                )
            conn.commit()
            after = self._count(conn)
            self._invalidate_cache()
            return before - after

    def clear(self, preserve_user: bool = False) -> int:
        """Delete known words from the database.

        Used by the "Rebuild Known Words DB" action (Issue #38) so that deck
        exclusions take effect for users of the local cache: the additive
        ``sync_with_anki`` never removes words, so a previously-synced excluded
        deck would otherwise stay cached forever.

        Args:
            preserve_user: When True, keep ``source='user'`` rows (the curated
                ignore list, Issue #42). Rebuild passes True so user-added words
                survive a cache rebuild; the default False keeps the full-wipe
                behaviour for any other caller.

        Returns:
            Number of rows removed.
        """
        with closing(self._connect()) as conn:
            before = self._count(conn)
            if preserve_user:
                conn.execute("DELETE FROM known_words WHERE source != 'user'")
            else:
                conn.execute("DELETE FROM known_words")
            conn.commit()
            after = self._count(conn)
            removed = before - after
        self._invalidate_cache()
        if preserve_user:
            log_summary(
                logger,
                "Known words rebuild done",
                preserve_user=preserve_user,
                before=before,
                after=after,
                removed=removed,
                user=after,
            )
        else:
            log_summary(
                logger,
                "Known words clear done",
                preserve_user=preserve_user,
                before=before,
                after=after,
                removed=removed,
                user=0,
            )
        return removed

    def clear_user(self) -> int:
        """Delete only the user-curated ignore list (Issue #42).

        Backs the "Reset User List" action in the Manage Known Words dialog.

        Returns:
            Number of ``source='user'`` rows removed.
        """
        with closing(self._connect()) as conn:
            before = self._count(conn)
            conn.execute("DELETE FROM known_words WHERE source = 'user'")
            conn.commit()
            after = self._count(conn)
            self._invalidate_cache()
            return before - after

    def word_count(self) -> int:
        """Return the total number of known words.

        Returns:
            Count of rows in the known_words table.
        """
        with closing(self._connect()) as conn:
            return self._count(conn)

    @staticmethod
    def _count(conn: sqlite3.Connection) -> int:
        """Count rows in the known_words table using an open connection."""
        cursor = conn.execute("SELECT COUNT(*) FROM known_words")
        return int(cursor.fetchone()[0])


def add_user_known_words(db_path: Path, forms: set[str]) -> int:
    """Persist curator-confirmed forms to the local known/ignore list.

    Encapsulates the user "mark known" rule shared by every mining tab's
    curation callback: build the DB ad hoc from the config path, write with
    ``source='user'``, and store the ``mined_form`` spelling as passed — never
    the lemma. Same pattern the settings tab uses for the rebuild action.

    The curator stages its marks and calls this only from a successful Confirm
    (D34-B), so cancelling a review writes nothing. Callers must not treat this
    as "persisted the moment the user clicked"; it is the commit step.

    Args:
        db_path: Path to the known-words SQLite database
            (``config.known_words_db_path``).
        forms: Set of ``mined_form`` strings the curator marked as known.

    Returns:
        Number of newly inserted rows (an in-place source upgrade is not
        counted as new).
    """
    db = KnownWordDB(db_path)
    db.initialize()
    return db.add_words(forms, source="user")
