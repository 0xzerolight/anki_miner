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

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import anki_miner.services._sqlite_index as _sqlite_index
from anki_miner.services._sqlite_index import open_readonly as open_readonly
from anki_miner.services._sqlite_index import read_meta as read_meta
from anki_miner.utils.text_utils import _is_kana_only, _is_kanji, katakana_to_hiragana

# v4: no table change — bumped to force a one-time reimport that re-runs the
# fixed Yomitan tag split (multi-word nbsp tag names were shattered on import).
# Stale (< SCHEMA_VERSION) indexes are dropped and the startup Reimport-All
# prompt + pre-run gate act on schema_ok; reimport is the migration.
SCHEMA_VERSION = 4

# Lone UTF-16 surrogates (U+D800–U+DFFF) have no valid UTF-8 encoding, so sqlite3
# raises ``UnicodeEncodeError: surrogates not allowed`` the moment such text is
# bound to a query. ``json.loads`` combines valid surrogate *pairs* into real code
# points, so anything left in this range is unpaired — typically corruption from a
# hand-converted dictionary (Issue #67). Scrub to U+FFFD at every write seam.
_SURROGATE_RE = re.compile("[\ud800-\udfff]")


def _scrub_surrogates(value: str | None) -> str | None:
    """Replace lone UTF-16 surrogates with U+FFFD so the value is UTF-8-encodable.

    Fast path: most strings encode cleanly and return unchanged; the regex only
    runs on the rare offending string.
    """
    if value is None:
        return None
    try:
        value.encode("utf-8")
        return value
    except UnicodeEncodeError:
        return _SURROGATE_RE.sub("�", value)


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS entries (
    id        INTEGER PRIMARY KEY,
    term      TEXT NOT NULL,
    reading   TEXT,
    content   TEXT NOT NULL,
    tags      TEXT NOT NULL DEFAULT '',
    rules     TEXT NOT NULL DEFAULT '',
    score     INTEGER DEFAULT 0,
    sequence  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_term    ON entries(term);
CREATE INDEX IF NOT EXISTS idx_reading ON entries(reading);

CREATE TABLE IF NOT EXISTS tags (
    name     TEXT PRIMARY KEY,
    category TEXT,
    ord      INTEGER,
    notes    TEXT,
    score    REAL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# Candidate-pool size fetched per word BEFORE the provider runs content-dedup,
# sequence grouping, and the display cap (indexed_provider._DISPLAY_LIMIT). Raised
# from the old flat 5 so duplicate-content rows (OVH-026) and lower-ranked
# homograph senses can no longer consume display slots ahead of dedup — the
# "dedup before cap" invariant (plan item 5.1). The provider caps the *rendered*
# senses; storage only bounds the pool.
#
# The pool cap is applied in PYTHON (``[:_LOOKUP_LIMIT]``) AFTER the render-path
# homograph scope (Rule A/B, U2) filters rows, in both ``lookup`` and
# ``lookup_many``. Capping in SQL (``LIMIT``) would truncate before scoping and
# could hide a survivor ranked past the cap — breaking the lookup↔lookup_many
# parity property (both fetch the full ordered candidate set, scope, THEN cap).
_LOOKUP_LIMIT = 20

# Reading-boost ranking. Ported from Yomitan
# ``Translator._sortTermDictionaryEntries`` (ext/js/language/translator.js,
# upstream e2ed450): ``matchPrimaryReading`` is the FIRST key of that ranking
# cascade — a sort BOOST, never a row filter. Here term-exact priority stays the
# leading key (unchanged 4.6 behavior); then rows whose stored (hiragana-folded)
# reading equals the token's contextual reading sort ahead of the other
# homograph's senses, while every non-matching row still survives below (boost,
# not filter). ``score DESC, sequence, id`` remain the lower tiebreaks.
#
# NULL-binding semantics: with no reading bound (wildcard), ``(reading = NULL)``
# is NULL for every row, so the key is inert and the ordering collapses to the
# pre-boost cascade — the ``reading=None`` path is byte-identical to 4.6 output.
#
# Explicit ``, id`` tiebreak makes single-word ordering fully deterministic (rows
# with equal priority/score and equal/NULL ``sequence`` would otherwise come back
# in unspecified query-plan order). This is what the batch ``lookup_many`` path
# reproduces with its final ``row_id`` sort, so keeping the tiebreak here
# guarantees ``lookup`` and ``lookup_many`` agree.
# ``term`` is projected (last column) so the render-path homograph scope (Rule
# A/B, U2) can classify each row term-exact vs reading-only in Python. No SQL
# LIMIT: the pool cap moves to Python AFTER scoping (see _LOOKUP_LIMIT note).
_LOOKUP_SQL = (
    "SELECT content, tags, sequence, term FROM entries "
    "WHERE term = ? OR reading = ? "
    "ORDER BY (term = ?) DESC, (reading = ?) DESC, score DESC, sequence, id"
)

# Same shape as _LOOKUP_SQL but also returns the ``rules`` column and takes no
# reading boost (fallback candidates carry no contextual reading). The lookup-miss
# fallback (plan item 5.2) needs each candidate row's rules to run Yomitan's POS
# check before rendering, so this is the schema-v3 ``rules`` column's first reader.
# ``term`` trails ``rules`` for the same U2 homograph-scope classification.
_LOOKUP_RULES_SQL = (
    "SELECT content, tags, sequence, rules, term FROM entries "
    "WHERE term = ? OR reading = ? "
    "ORDER BY (term = ?) DESC, score DESC, sequence, id"
)


@dataclass(frozen=True)
class DictRow:
    """One importable row. Mirrors the entries table schema."""

    term: str
    reading: str | None
    content: str
    tags: str = ""
    # Yomitan term-bank ``ruleIdentifiers`` (entry[3]): space-separated
    # deinflection condition flags (e.g. "v5 vs"). Stored for the schema-v3
    # deinflector-fallback consumer (plan item 5.2); no reader in 4.6.
    rules: str = ""
    score: int = 0
    sequence: int | None = None


@dataclass(frozen=True)
class TagMeta:
    """One tag-metadata row. Mirrors the ``tags`` table schema.

    Ported from Yomitan ``DictionaryImporter._convertTagBankEntry``
    (ext/js/dictionary/dictionary-importer.js, upstream e2ed450): a tag-bank
    5-tuple ``[name, category, order, notes, score]`` — ``order`` is stored in
    the ``ord`` column (SQL keyword clash) and drives chip sorting.
    """

    name: str
    category: str
    ord: int
    notes: str
    score: float


def _fold_reading(reading: str | None) -> str | None:
    """Hiragana-fold a stored reading (katakana → hiragana), preserving None.

    Readings are stored hiragana-normalized so a katakana loanword reading and
    its hiragana equivalent collate to one key; lookup folds the query side to
    match (schema v3, plan item 5.1 match-by-kana invariant).
    """
    return katakana_to_hiragana(reading) if reading is not None else None


def _homograph_keep_mask(word: str, rows: list[tuple[str, str]]) -> list[bool]:
    """Render-path homograph scope (U2): a keep-mask aligned to ``rows``.

    ``rows`` are ``(term, content)`` pairs for the rows a lookup fetched for
    ``word`` (each already matched ``term = word`` OR the folded reading), so a
    row is *term-exact* iff its term equals ``word`` and *reading-only* otherwise.
    Two rules drop wrong-homograph reading matches from the RENDERED definition
    (existence/attestation probes bypass this — see ``lookup_many``'s
    ``scope_homographs`` flag):

    * **Rule A** — at least one term-exact row exists ⇒ keep the term-exact rows
      and drop reading-only homographs whose gloss (``content``) is NOT already
      contributed by a term-exact row (レイド keeps its raid senses and drops 零度
      "zero degrees"). The content carve-out preserves the dedup-before-cap tag
      union (OVH-026): a dictionary that double-keys ONE entry under both a kanji
      term (日本語, reading にほんご) and the bare kana term (にほんご) still unions
      both rows' tags on a kana query — that reading-only row is the SAME gloss,
      not a wrong homograph. Monotone-safe: term-exact rows always survive, so a
      word with one can never be emptied.
    * **Rule B** — kana-only query with NO term-exact row ⇒ keep only reading
      matches whose term carries at least one kanji (しゃべる keeps 喋る, drops the
      kana-term シャベル). May legitimately empty a junk kana front (accepted).
      Same-script kanji-vs-kanji ordering (汁 vs 知る) is out of scope.

    Any other case (kanji query with no term-exact row — reading matches against a
    kanji query are impossible) leaves the set intact.
    """
    term_exact = [term == word for term, _ in rows]
    if any(term_exact):
        exact_contents = {content for (_, content), ex in zip(rows, term_exact, strict=True) if ex}
        return [ex or content in exact_contents for (_, content), ex in zip(rows, term_exact, strict=True)]
    if _is_kana_only(word):
        return [any(_is_kanji(c) for c in term) for term, _ in rows]
    return [True] * len(rows)


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
            # reading is stored hiragana-folded so katakana/hiragana readings
            # collate to one key (schema v3); lookup folds the query side too.
            batch.append(
                (
                    _scrub_surrogates(row.term),
                    _scrub_surrogates(_fold_reading(row.reading)),
                    _scrub_surrogates(row.content),
                    _scrub_surrogates(row.tags),
                    _scrub_surrogates(row.rules),
                    row.score,
                    row.sequence,
                )
            )
            if len(batch) >= batch_size:
                conn.executemany(
                    "INSERT INTO entries (term, reading, content, tags, rules, score, sequence) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    batch,
                )
                total += len(batch)
                batch.clear()
        if batch:
            conn.executemany(
                "INSERT INTO entries (term, reading, content, tags, rules, score, sequence) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                batch,
            )
            total += len(batch)
        conn.commit()
    finally:
        conn.close()
    return total


def write_tags(db_path: Path, tags: Iterable[TagMeta]) -> int:
    """Insert tag-metadata rows into the ``tags`` table. Returns total written.

    Uses ``INSERT OR REPLACE`` on the ``name`` primary key so a tag appearing in
    more than one ``tag_bank_*.json`` (or duplicated between a tag bank and the
    legacy ``index.json`` ``tagMeta``) collapses to its last-seen definition
    rather than raising. Text fields are surrogate-scrubbed like every other
    write seam (Issue #67).
    """
    total = 0
    conn = sqlite3.connect(db_path)
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO tags (name, category, ord, notes, score) VALUES (?, ?, ?, ?, ?)",
            (
                (
                    _scrub_surrogates(t.name),
                    _scrub_surrogates(t.category),
                    t.ord,
                    _scrub_surrogates(t.notes),
                    t.score,
                )
                for t in tags
            ),
        )
        total = conn.total_changes
        conn.commit()
    finally:
        conn.close()
    return total


def read_tags(conn: sqlite3.Connection) -> dict[str, TagMeta]:
    """Return all tag-metadata rows keyed by tag name.

    Consumed by the provider's lazy per-dictionary tag cache to expand tag
    names into hover chips. A dictionary with no ``tag_bank`` / legacy
    ``tagMeta`` simply yields an empty dict (every tag then falls back to the
    italic token line).
    """
    result: dict[str, TagMeta] = {}
    for name, category, ord_, notes, score in conn.execute("SELECT name, category, ord, notes, score FROM tags"):
        result[name] = TagMeta(
            name=name,
            category=category if category is not None else "",
            ord=ord_ if ord_ is not None else 0,
            notes=notes if notes is not None else "",
            score=float(score) if score is not None else 0.0,
        )
    return result


def write_meta(db_path: Path, items: dict[str, str]) -> None:
    """Upsert meta rows, surrogate-scrubbing each value (Issue #67), and refresh
    the ``meta.json`` sidecar so the next ``read_meta_cached`` avoids re-opening
    SQLite. Thin wrapper over the shared meta writer."""
    _sqlite_index.write_meta(db_path, items, value_transform=_scrub_surrogates)


def read_meta_cached(db_path: Path) -> dict[str, str]:
    """Read meta rows via the ``meta.json`` sidecar when fresh, falling back to
    :func:`read_meta` when the sidecar is missing/stale/corrupt.

    Used by ``DictionaryRegistry.load()`` to skip the SQLite open on startup when
    nothing changed since the last run. Passes the module-level ``read_meta`` so
    tests patching ``...dictionary.storage.read_meta`` observe the fall-through.
    """
    return _sqlite_index.read_meta_cached(db_path, read_meta)


def lookup(conn: sqlite3.Connection, word: str, reading: str | None = None) -> list[tuple[str, str, int | None]]:
    """Return up to ``_LOOKUP_LIMIT`` (content, tags, sequence) triples matching
    word (term or folded reading), reading-boosted then ranked.

    ``reading`` is the token's contextual kana reading (e.g. ``w.lemma_reading``)
    and acts as a ranking BOOST only: rows whose stored reading equals it sort
    first, the rest survive below. ``None`` = wildcard (no boost) = 4.6 behavior.

    Readings are stored hiragana-folded, so the reading-match WHERE clause binds
    the folded query word and the boost binds the folded contextual reading,
    while the term comparison (and the ``(term = ?)`` priority tiebreak) binds the
    raw word — a katakana query still matches a kanji headword's folded reading
    (schema v3, touch point a).
    """
    folded_word = katakana_to_hiragana(word)
    folded_boost = katakana_to_hiragana(reading) if reading is not None else None
    rows = conn.execute(_LOOKUP_SQL, (word, folded_word, word, folded_boost)).fetchall()
    # rows: (content, tags, sequence, term). Scope homographs (Rule A/B) over the
    # ORDER BY-sorted candidate set, THEN apply the pool cap — matching the
    # filter-before-cap order ``lookup_many`` uses so both stay row-for-row equal.
    keep = _homograph_keep_mask(word, [(row[3], row[0]) for row in rows])
    kept = [row for row, k in zip(rows, keep, strict=True) if k]
    return [(row[0], row[1], row[2]) for row in kept[:_LOOKUP_LIMIT]]


def lookup_with_rules(conn: sqlite3.Connection, word: str) -> list[tuple[str, str, int | None, str]]:
    """Return (content, tags, sequence, rules) rows matching ``word`` by term or
    folded reading, ranked like :func:`lookup` (no reading boost).

    The lookup-miss fallback (plan item 5.2) probes deinflection/variant
    candidates through this so it can POS-check each row's ``rules`` before
    rendering. A NULL/absent ``rules`` column normalises to ``""`` (accept
    unconditionally at the caller). Katakana folding matches ``lookup``: a
    katakana candidate still matches a kanji headword's hiragana-folded reading.
    """
    folded_word = katakana_to_hiragana(word)
    rows = conn.execute(_LOOKUP_RULES_SQL, (word, folded_word, word)).fetchall()
    # rows: (content, tags, sequence, rules, term). Render-side, so scope
    # homographs (Rule A/B) then apply the pool cap, mirroring ``lookup``.
    keep = _homograph_keep_mask(word, [(row[4], row[0]) for row in rows])
    kept = [row for row, k in zip(rows, keep, strict=True) if k]
    return [(row[0], row[1], row[2], row[3] if row[3] is not None else "") for row in kept[:_LOOKUP_LIMIT]]


# sqlite's default SQLITE_MAX_VARIABLE_NUMBER is 999. lookup_many binds each
# word twice (term IN + reading IN), so a single chunk may use at most
# 2 * _BIND_CHUNK variables. Keep the product comfortably under the cap.
_BIND_CHUNK = 450


def lookup_many(
    conn: sqlite3.Connection, pairs: list[tuple[str, str | None]], scope_homographs: bool = True
) -> dict[str, list[tuple[str, str, int | None]]]:
    """Batch variant of :func:`lookup`.

    ``pairs`` is a list of ``(word, reading | None)`` — each word's contextual
    reading boosts *that word's own bucket* (``None`` = wildcard, no boost).
    Runs ONE query per chunk (``WHERE term IN (...) OR reading IN (...)``)
    instead of one query per word, then reproduces ``_LOOKUP_SQL``'s reading
    boost, ordering, and pool cap in Python so each per-word result is
    byte-identical, row-for-row, to ``lookup(conn, word, reading)``.

    ``scope_homographs`` (default ``True``) applies the render-path Rule A/B
    homograph scope (:func:`_homograph_keep_mask`) per word before the sort/cap,
    matching ``lookup``. Set it ``False`` for the existence/attestation probes
    (``has_offline_definitions`` and the kana-recovery attest path) that must keep
    the historical unfiltered term-OR-reading semantics — otherwise a kana-front
    card attested only via a kana-term reading row would silently vanish. With it
    ``False`` this function is byte-identical to its pre-U2 behavior.

    Returns a dict keyed by every requested word (duplicate words collapse to the
    first reading seen). A word with no matches maps to ``[]``, mirroring
    ``lookup``'s empty-result case.
    """
    # Preserve first-seen order; collapse duplicate words to one bucket (first
    # reading wins, matching the caller's own word-level dedup).
    unique_pairs: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for word, reading in pairs:
        if word not in seen:
            seen.add(word)
            unique_pairs.append((word, reading))

    result: dict[str, list[tuple[str, str, int | None]]] = {w: [] for w, _ in unique_pairs}
    if not unique_pairs:
        return result

    # Per-word folded boost reading (hiragana-folded to match stored readings);
    # None keeps the wildcard (no-boost) ordering for that word.
    boost_by_word: dict[str, str | None] = {
        w: (katakana_to_hiragana(r) if r is not None else None) for w, r in unique_pairs
    }
    unique_words = [w for w, _ in unique_pairs]

    for start in range(0, len(unique_words), _BIND_CHUNK):
        chunk = unique_words[start : start + _BIND_CHUNK]
        # Readings are stored hiragana-folded, so the ``reading IN`` clause must
        # bind the folded query words (touch point b) — a katakana requested
        # word still fetches the row whose folded reading it matches.
        folded_chunk = [katakana_to_hiragana(w) for w in chunk]
        placeholders = ", ".join("?" for _ in chunk)
        sql = (
            "SELECT id, term, reading, content, tags, score, sequence FROM entries "
            f"WHERE term IN ({placeholders}) OR reading IN ({placeholders})"
        )
        rows = conn.execute(sql, (*chunk, *folded_chunk)).fetchall()

        # Bucket each fetched row to every requested word it can satisfy. A row
        # may match one word by term and a different word by reading. Each entry
        # carries the sort keys that reproduce _LOOKUP_SQL's
        # "ORDER BY (term=?) DESC, (reading=?) DESC, score DESC, sequence", plus a
        # final ``id`` tiebreak:
        #   * term_priority: 0 when this row's term equals the word (DESC puts
        #     term matches first), else 1.
        #   * reading_priority: mirrors the reading boost ``(reading=?) DESC``
        #     against THIS word's contextual reading (0 match / 1 differ / 2 NULL;
        #     constant when the word has no boost). See _reading_priority.
        #   * score_key: (is_null, -score) mirrors ``score DESC`` with NULL last.
        #   * _seq_key(sequence): NULL-aware ascending sequence tiebreak.
        #   * row_id: SQLite resolves equal (priority, score, sequence) ties by
        #     rowid ascending under the single-word query's MULTI-INDEX OR plan;
        #     replaying it here keeps lookup_many byte-identical to lookup.
        chunk_set = set(chunk)
        # Hiragana-keyed reverse map (touch point c): folded requested word →
        # the requested word(s) that fold to it. A reading-only hit is assigned
        # back through this map so a katakana requested word (whose raw form no
        # longer equals the folded stored reading) is not silently dropped —
        # the divergence that would break the lookup_many == lookup invariant.
        reading_reverse: dict[str, list[str]] = {}
        for w, wf in zip(chunk, folded_chunk, strict=True):
            reading_reverse.setdefault(wf, []).append(w)
        # ``term`` (index 5) is carried on each entry so the U2 homograph scope
        # can classify term-exact vs reading-only per word before the cap; the
        # sort key stays the first five fields and the result unpack still takes
        # the trailing (content, tags, sequence).
        buckets: dict[str, list[tuple[int, int, tuple[int, int], tuple[int, int], int, str, str, str, int | None]]] = {
            w: [] for w in chunk
        }
        for row_id, term, reading, content, tags, score, sequence in rows:
            tags_val = tags if tags is not None else ""
            folded_reading = katakana_to_hiragana(reading) if reading is not None else None
            seq_key = _seq_key(sequence)
            score_key = _score_key(score)
            # A row satisfies a word via term OR reading. _LOOKUP_SQL's
            # ``term=? OR reading=?`` returns each row ONCE per word even when
            # both columns match, so collapse to one entry per requested word,
            # letting the term match (priority 0) win over a reading-only one.
            matched: dict[str, int] = {}
            if term in chunk_set:
                matched[term] = 0
            if folded_reading is not None:
                for w in reading_reverse.get(folded_reading, ()):
                    matched.setdefault(w, 1)
            for w, term_priority in matched.items():
                reading_priority = _reading_priority(folded_reading, boost_by_word[w])
                buckets[w].append(
                    (term_priority, reading_priority, score_key, seq_key, row_id, term, content, tags_val, sequence)
                )

        for w, entries in buckets.items():
            if scope_homographs:
                # Filter BEFORE sort/cap: order-independent per-row predicate, so
                # scoping then sorting equals ``lookup``'s scope-the-sorted-set.
                # e[5]=term, e[6]=content (see the entry tuple above).
                keep = _homograph_keep_mask(w, [(e[5], e[6]) for e in entries])
                entries = [e for e, k in zip(entries, keep, strict=True) if k]
            entries.sort(key=lambda e: (e[0], e[1], e[2], e[3], e[4]))
            result[w] = [(content, tags, seq) for *_keys, content, tags, seq in entries[:_LOOKUP_LIMIT]]

    return result


# terms_exist binds each term ONCE (single-column IN), so the chunk can be
# larger than lookup_many's _BIND_CHUNK while staying under sqlite's default
# SQLITE_MAX_VARIABLE_NUMBER of 999.
_EXIST_CHUNK = 900


def terms_exist(conn: sqlite3.Connection, terms: list[str]) -> set[str]:
    """Return the subset of ``terms`` present as an exact ``entries.term`` match.

    Reading-column matches deliberately do NOT count: the compound matcher
    asks "is this string a dictionary headword", not "can this kana string
    be looked up somehow". Matching on reading would attest every kana
    sequence that happens to be some entry's reading and cause spurious
    token merges.
    """
    unique = list(dict.fromkeys(terms))
    found: set[str] = set()
    for start in range(0, len(unique), _EXIST_CHUNK):
        chunk = unique[start : start + _EXIST_CHUNK]
        placeholders = ", ".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT DISTINCT term FROM entries WHERE term IN ({placeholders})",
            chunk,
        ).fetchall()
        found.update(row[0] for row in rows)
    return found


def terms_readings(conn: sqlite3.Connection, terms: list[str]) -> dict[str, list[str]]:
    """Attested readings per exact headword, best-first (entry ``score`` DESC).

    Companion to :func:`terms_exist` for the merged-compound reading
    attestation pass (``morphology.attest_merged_readings``): "which readings
    does the dictionary attest for this exact headword". Rows with a NULL or
    empty reading are skipped — some JMdict variant-form rows (mazegaki せん越,
    katakana ケガ人) ship no reading and can attest nothing. Readings come back
    as stored (hiragana-folded at import via ``_fold_reading``), deduped,
    score-ordered so index 0 is the dictionary's best entry for the term.
    """
    unique = list(dict.fromkeys(terms))
    found: dict[str, list[str]] = {}
    for start in range(0, len(unique), _EXIST_CHUNK):
        chunk = unique[start : start + _EXIST_CHUNK]
        placeholders = ", ".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT term, reading FROM entries WHERE term IN ({placeholders}) "
            "AND reading IS NOT NULL AND reading != '' ORDER BY score DESC",
            chunk,
        ).fetchall()
        for term, reading in rows:
            readings = found.setdefault(term, [])
            if reading not in readings:
                readings.append(reading)
    return found


# Sort key mirroring SQLite "ORDER BY (reading = ?) DESC" for the reading boost.
# With no boost bound (folded_boost is None), the SQL predicate is NULL for every
# row so all rows tie — return a constant. With a boost: a row whose folded
# reading equals it ranks first (SQL true 1), a differing non-NULL reading next
# (SQL false 0), and a NULL reading last (SQL NULL sorts last under DESC).
def _reading_priority(folded_row_reading: str | None, folded_boost: str | None) -> int:
    if folded_boost is None:
        return 0
    if folded_row_reading is None:
        return 2
    return 0 if folded_row_reading == folded_boost else 1


# Sort key mirroring SQLite "ORDER BY sequence": NULL sorts before any value.
# (is_not_null, value) where NULL -> (0, 0) sorts ahead of any integer.
def _seq_key(sequence: int | None) -> tuple[int, int]:
    if sequence is None:
        return (0, 0)
    return (1, sequence)


# Sort key mirroring SQLite "ORDER BY score DESC": NULL sorts last.
# (is_null, -score) where a present score -> (0, -score) sorts ahead of any
# NULL -> (1, 0); among present scores, -score ascending == score descending.
# Unreachable in practice (the importer coerces score to int and the schema
# defaults it to 0), but keeps lookup_many byte-identical to the SQL lookup.
def _score_key(score: int | None) -> tuple[int, int]:
    if score is None:
        return (1, 0)
    return (0, -score)
