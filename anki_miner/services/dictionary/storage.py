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
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, NamedTuple

import anki_miner.services._sqlite_index as _sqlite_index
from anki_miner.exceptions import OperationCancelled
from anki_miner.services._sqlite_index import open_readonly as open_readonly
from anki_miner.services._sqlite_index import read_meta as read_meta
from anki_miner.utils.text_utils import _is_kana_only, _is_kanji, katakana_to_hiragana

# v6: no table change — bumped to force a one-time reimport with NFC-normalized
# term and reading keys.
# Stale (< SCHEMA_VERSION) indexes are dropped and the startup Reimport-All
# prompt + pre-run gate act on schema_ok; reimport is the migration.
SCHEMA_VERSION = 6

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


_TABLES_SQL = """
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

# The lookup indexes every reader needs. Split out of the table DDL so an
# importer can populate first and build them once, instead of maintaining the
# B-trees across every one of a million inserts. ``idx_sequence`` serves the
# redirect-row resolution (_fetch_rows_for_sequences); pre-existing indexes
# without it are still correct — the resolution query just falls back to a
# table scan, so no schema bump / forced reimport.
_LOOKUP_INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_term     ON entries(term);
CREATE INDEX IF NOT EXISTS idx_reading  ON entries(reading);
CREATE INDEX IF NOT EXISTS idx_sequence ON entries(sequence);
"""

_SCHEMA_SQL = _TABLES_SQL + _LOOKUP_INDEXES_SQL

# Candidate-pool size fetched per word BEFORE the provider runs content-dedup,
# sequence grouping, and the display cap (indexed_provider._DISPLAY_LIMIT). Raised
# from the old flat 5 so duplicate-content rows (OVH-026) and lower-ranked
# homograph senses can no longer consume display slots ahead of dedup — the
# "dedup before cap" invariant (plan item 5.1). The provider caps the *rendered*
# senses; storage only bounds the pool.
#
# The pool cap is applied in PYTHON (``[:_LOOKUP_LIMIT]``) AFTER the render-path
# homograph scope (Rule A/B, U2) filters rows, in both ``lookup`` and
# ``lookup_many``. Neither one caps rows in SQL: capping there would truncate
# before scoping and could hide a survivor ranked past the cap, so
# ``lookup_many`` is row-for-row identical to ``lookup`` on every input, not
# merely on inputs below some threshold. A hyper-common kana reading attesting
# thousands of full-content rows is paid for in memory instead; the chunk size
# (``_LOOKUP_MANY_CHUNK``) is what bounds how many such words can land in one
# fetchall().
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


# Tag-bank ``category`` values (TagMeta.category) that mark an entry as a
# common/frequent headword. A dictionary is "commonness-aware" iff its tags
# table defines at least one tag in one of these categories; a row is "common"
# iff it carries such a tag. Category-based, NOT table-presence: jitendex uses
# 'frequent'/'popular', and so does the Yomitan JMdict build (⭐ → 'popular';
# news·k/ichi/spec/gai → 'frequent'). The legacy XML-derived jmdict import
# writes no commonness categories ('partOfSpeech'/'name'/'' only → unaware),
# and a monolingual dict ships an empty tags table (also unaware). Single
# source of truth shared by ``row_is_common`` and the provider's
# ``commonness_aware`` property (U10 infra).
COMMON_TAG_CATEGORIES = frozenset({"frequent", "popular"})


class AttestRow(NamedTuple):
    """One attesting row for the commonness/quality probes (U10 infra).

    ``match_kind`` is ``'term'`` (row's ``term`` equals the queried word) or
    ``'reading'`` (row's hiragana-folded ``reading`` equals the folded word).
    ``rules``/``tags`` are the row's raw columns so the provider can classify
    POS (rules) and commonness (tags, via :func:`row_is_common`) without a
    second query.
    """

    match_kind: str
    rules: str
    tags: str


def row_is_common(tags_str: str, tag_meta: dict[str, TagMeta]) -> bool:
    """True iff any of ``tags_str``'s tags is categorized as common/frequent.

    ``tags_str`` is an entry row's space-separated ``tags`` column; each token
    is a full tag name looked up in ``tag_meta`` (the dict's ``tags`` table).
    Split on ASCII space ONLY — Yomitan tag NAMES may contain an internal
    non-breaking space (e.g. ``'priority form'``), so a token stays whole
    and matches its ``tags``-table key. A tag with no table row, or a category
    outside :data:`COMMON_TAG_CATEGORIES`, does not mark the row common.
    """
    if not tags_str:
        return False
    for tag in tags_str.split(" "):
        if not tag:
            continue
        meta = tag_meta.get(tag)
        if meta is not None and meta.category in COMMON_TAG_CATEGORIES:
            return True
    return False


def _fold_reading(reading: str | None) -> str | None:
    """Hiragana-fold a stored reading (katakana → hiragana), preserving None.

    Readings are stored hiragana-normalized so a katakana loanword reading and
    its hiragana equivalent collate to one key; lookup folds the query side to
    match (schema v3, plan item 5.1 match-by-kana invariant).
    """
    return katakana_to_hiragana(unicodedata.normalize("NFC", reading)) if reading is not None else None


def _homograph_keep_mask(word: str, rows: list[tuple[str, str]], lemma: str | None = None) -> list[bool]:
    """Render-path homograph scope (U2): a keep-mask aligned to ``rows``.

    ``rows`` are ``(term, content)`` pairs for the rows a lookup fetched for
    ``word`` (each already matched ``term = word`` OR the folded reading), so a
    row is *term-exact* iff its term equals ``word`` and *reading-only* otherwise.
    Three rules drop wrong-homograph reading matches from the RENDERED definition
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
    * **Rule A′** — no term-exact row, but the caller supplied the token's
      lemma and at least one row's term equals it ⇒ keep the lemma-exact rows
      (plus same-content duplicates, the Rule A carve-out). This is the
      kana-front fix: mined_form ゆう (lemma 言う) resolves purely through the
      folded-reading scan where every ゆう-reading homograph qualifies and
      score ranking buries 言う under 有/夕/結う — the tokenizer already chose
      the lexeme, so its lemma names the right rows. Kanji fronts can't reach
      this rule: a kanji query fetches no reading matches, so it either has
      term-exact rows (Rule A) or zero rows (miss → 5.2 fallback).
    * **Rule B** — kana-only query with NO term-exact (or lemma-exact) row ⇒
      keep only reading matches whose term carries at least one kanji (しゃべる
      keeps 喋る, drops the kana-term シャベル). May legitimately empty a junk
      kana front (accepted). Same-script kanji-vs-kanji ordering (汁 vs 知る)
      is out of scope.

    Any other case (kanji query with no term-exact row — reading matches against a
    kanji query are impossible) leaves the set intact.
    """
    term_exact = [term == word for term, _ in rows]
    if any(term_exact):
        exact_contents = {content for (_, content), ex in zip(rows, term_exact, strict=True) if ex}
        return [ex or content in exact_contents for (_, content), ex in zip(rows, term_exact, strict=True)]
    if lemma and lemma != word:
        lemma_exact = [term == lemma for term, _ in rows]
        if any(lemma_exact):
            lemma_contents = {content for (_, content), ex in zip(rows, lemma_exact, strict=True) if ex}
            return [ex or content in lemma_contents for (_, content), ex in zip(rows, lemma_exact, strict=True)]
    if _is_kana_only(word):
        return [any(_is_kanji(c) for c in term) for term, _ in rows]
    return [True] * len(rows)


def _connect_for_bulk_write(db_path: Path) -> sqlite3.Connection:
    """Open *db_path* tuned for a one-shot bulk load.

    Importers write into a staging database that is renamed into place only
    after the whole import succeeds, so durability during the load buys nothing:
    a crash leaves staging bytes that are discarded, never a promoted index. The
    defaults (rollback journal, ``synchronous=FULL``) cost an fsync per batch
    and a journal write per page for exactly that discarded state.
    """
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-16000")
    return conn


def create_index(db_path: Path, *, with_lookup_indexes: bool = True) -> None:
    """Create a fresh dictionary index at db_path. Idempotent (uses IF NOT EXISTS).

    ``with_lookup_indexes=False`` creates the tables only, leaving ``idx_term``
    and ``idx_reading`` to :func:`create_lookup_indexes` after the rows land.
    Importers use it; every other caller gets the fully indexed database the
    default has always produced.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        conn.executescript(_SCHEMA_SQL if with_lookup_indexes else _TABLES_SQL)
        conn.commit()
    finally:
        conn.close()


def create_lookup_indexes(db_path: Path) -> None:
    """Build the ``entries`` lookup indexes. Idempotent (uses IF NOT EXISTS).

    Building once over a populated table is markedly cheaper than maintaining
    the same two B-trees across every insert, so importers defer to this.
    """
    conn = _connect_for_bulk_write(db_path)
    try:
        conn.executescript(_LOOKUP_INDEXES_SQL)
        conn.commit()
    finally:
        conn.close()


def ensure_sequence_index(db_path: Path) -> None:
    """Create any missing lookup index (notably ``idx_sequence``) on an
    existing index file. v6 indexes imported before redirect resolution lack
    ``idx_sequence`` and pay a full table scan per redirect batch; this
    backfills it once, without a schema bump (the data is correct). Refreshes
    the ``meta.json`` sidecar mtime so the fast path is not invalidated by the
    write.

    Raises on failure (read-only filesystem, locked DB) — this function does
    NOT swallow; the caller (``IndexedDictProvider.load()``) catches and logs
    so the scan fallback stays correct instead of failing the whole load."""
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_LOOKUP_INDEXES_SQL)
        conn.commit()
    finally:
        conn.close()
    sidecar = db_path.parent / "meta.json"
    if sidecar.is_file():
        sidecar.touch()


def bulk_insert(
    db_path: Path,
    rows: Iterable[DictRow],
    batch_size: int = 5000,
    *,
    progress: Callable[[int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> int:
    """Insert rows in batched transactions. Returns total inserted.

    ``progress`` receives the cumulative inserted-row count after each
    ``executemany``. ``cancel_check`` is polled before each batch and aborts
    with ``OperationCancelled("Import cancelled")`` when true.

    The sqlite3 `with` context manager commits/rolls back but does NOT close
    the connection — we close explicitly so the db file is not held open
    across the importer's staging-dir cleanup (matters on Windows).
    """
    total = 0
    conn = _connect_for_bulk_write(db_path)
    try:
        batch: list[tuple] = []

        def flush_batch() -> None:
            nonlocal total
            if not batch:
                return
            if cancel_check is not None and cancel_check():
                raise OperationCancelled("Import cancelled")
            conn.executemany(
                "INSERT INTO entries (term, reading, content, tags, rules, score, sequence) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                batch,
            )
            total += len(batch)
            batch.clear()
            if progress is not None:
                progress(total)

        for row in rows:
            # reading is stored hiragana-folded so katakana/hiragana readings
            # collate to one key (schema v3); lookup folds the query side too.
            batch.append(
                (
                    _scrub_surrogates(unicodedata.normalize("NFC", row.term)),
                    _scrub_surrogates(_fold_reading(row.reading)),
                    _scrub_surrogates(row.content),
                    _scrub_surrogates(row.tags),
                    _scrub_surrogates(row.rules),
                    row.score,
                    row.sequence,
                )
            )
            if len(batch) >= batch_size:
                flush_batch()
        flush_batch()
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
    conn = sqlite3.connect(db_path, timeout=5.0)
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


# ---------------------------------------------------------------------------
# Redirect ("pointer") rows — the yomidevs / Jitendex JMdict-family exports.
#
# Those dictionaries emit JMdict search-only spellings (sK/sk keys, e.g.
# お互いさま) as dedicated pointer rows whose whole glossary is "⟶ お互い様"
# (Jitendex 4.7 "redirections"). Their convention, verified exact on both
# catalog dicts (Jitendex 2026-06-06: 136,905/432,643 rows; JMdict 2026-06-28:
# 19,796/523,745): a redirect row's ``sequence`` is the NEGATION of the
# canonical entry's sequence, its content carries the U+27F6 arrow, and no
# real row has either marker. An arrow rendered into a card's definition field
# is junk (nothing to click on a card), so the read paths below splice each
# surviving redirect row's canonical rows in at the redirect's own rank.
# One hop only — targets are positive and a positive row is never a redirect.
# A redirect whose target is absent contributes nothing, so a fully-redirect
# result collapses to a miss and the provider chain (other dicts, deinflection
# fallback, Jisho) gets its shot.
#
# Both predicate halves are required: a foreign dictionary using negative
# sequences for real content (no arrow) must pass through untouched.
_REDIRECT_ARROW = "⟶"  # ⟶


def _is_redirect_row(content: str, sequence: int | None) -> bool:
    return sequence is not None and sequence < 0 and _REDIRECT_ARROW in content


def _fetch_rows_for_sequences(
    conn: sqlite3.Connection, sequences: list[int]
) -> dict[int, list[tuple[str, str, int | None, str]]]:
    """(content, tags, sequence, rules) rows per requested sequence, each list
    in ``score DESC, sequence, id`` order so a resolved stand-in keeps the
    canonical entry's own internal ranking. Pre-``idx_sequence`` indexes serve
    this with a table scan — batched by the callers and only paid when a
    redirect row actually survived scoping."""
    grouped: dict[int, list[tuple[tuple[int, int], tuple[int, int], int, str, str, int | None, str]]] = {}
    for start in range(0, len(sequences), _EXIST_CHUNK):
        chunk = sequences[start : start + _EXIST_CHUNK]
        placeholders = ", ".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT id, content, tags, rules, score, sequence FROM entries WHERE sequence IN ({placeholders})",
            chunk,
        ).fetchall()
        for row_id, content, tags, rules, score, sequence in rows:
            grouped.setdefault(sequence, []).append(
                (
                    _score_key(score),
                    _seq_key(sequence),
                    row_id,
                    content,
                    tags if tags is not None else "",
                    sequence,
                    rules if rules is not None else "",
                )
            )
    return {
        seq: [(content, tags, sequence, rules) for _s, _q, _i, content, tags, sequence, rules in sorted(entries)]
        for seq, entries in grouped.items()
    }


def _substitute_redirect_rows(
    conn: sqlite3.Connection,
    rows: list[tuple[str, str, int | None]] | list[tuple[str, str, int | None, str]],
    resolved_by_seq: dict[int, list[tuple[str, str, int | None, str]]] | None = None,
    *,
    with_rules: bool = False,
) -> list[tuple[str, str, int | None]] | list[tuple[str, str, int | None, str]]:
    """Splice each redirect row's canonical rows in at the redirect's own rank.

    ``rows`` are one word's projected, scoped, sorted result tuples —
    ``(content, tags, sequence)`` or, with ``with_rules``,
    ``(content, tags, sequence, rules)`` — BEFORE the ``_LOOKUP_LIMIT`` cap.
    Non-redirect rows keep their positions; a target already spliced for this
    word is not spliced twice. No redirect rows ⇒ input returned as-is, so the
    hot path pays no query. ``resolved_by_seq`` lets ``lookup_many`` share one
    batch-wide fetch; ``None`` fetches for just these rows."""
    targets = list(dict.fromkeys(-row[2] for row in rows if row[2] is not None and _is_redirect_row(row[0], row[2])))
    if not targets:
        return rows
    if resolved_by_seq is None:
        resolved_by_seq = _fetch_rows_for_sequences(conn, targets)
    out: list[tuple[str, str, int | None]] | list[tuple[str, str, int | None, str]] = []
    spliced: set[int] = {
        row[2] for row in rows if row[2] is not None and row[2] > 0 and not _is_redirect_row(row[0], row[2])
    }
    for row in rows:
        if row[2] is None or not _is_redirect_row(row[0], row[2]):
            out.append(row)  # type: ignore[arg-type]
            continue
        target = -row[2]
        if target in spliced:
            continue
        spliced.add(target)
        for content, tags, sequence, rules in resolved_by_seq.get(target, []):
            out.append((content, tags, sequence, rules) if with_rules else (content, tags, sequence))  # type: ignore[arg-type]
    return out


def lookup(
    conn: sqlite3.Connection, word: str, reading: str | None = None, lemma: str | None = None
) -> list[tuple[str, str, int | None]]:
    """Return up to ``_LOOKUP_LIMIT`` (content, tags, sequence) triples matching
    word (term or folded reading), reading-boosted then ranked.

    ``reading`` is the token's contextual kana reading (e.g. ``w.lemma_reading``)
    and acts as a ranking BOOST only: rows whose stored reading equals it sort
    first, the rest survive below. ``None`` = wildcard (no boost) = 4.6 behavior.

    ``lemma`` is the token's UniDic lemma; it feeds the Rule A′ homograph scope
    (see :func:`_homograph_keep_mask`) so a kana front (ゆう, lemma 言う) keeps
    its own lexeme's rows instead of every same-reading homograph. ``None``
    keeps pre-A′ behavior.

    Readings are stored hiragana-folded, so the reading-match WHERE clause binds
    the folded query word and the boost binds the folded contextual reading,
    while the term comparison (and the ``(term = ?)`` priority tiebreak) binds the
    raw word — a katakana query still matches a kanji headword's folded reading
    (schema v3, touch point a).
    """
    word = unicodedata.normalize("NFC", word)
    folded_word = katakana_to_hiragana(word)
    folded_boost = _fold_reading(reading)
    normalized_lemma = unicodedata.normalize("NFC", lemma) if lemma else None
    rows = conn.execute(_LOOKUP_SQL, (word, folded_word, word, folded_boost)).fetchall()
    # rows: (content, tags, sequence, term). Scope homographs (Rule A/A′/B) over
    # the ORDER BY-sorted candidate set, THEN apply the pool cap — matching the
    # filter-before-cap order ``lookup_many`` uses. Row-for-row equal to
    # ``lookup_many`` on every input: neither fetch caps rows in SQL (see the
    # ``_LOOKUP_LIMIT`` comment above).
    keep = _homograph_keep_mask(word, [(row[3], row[0]) for row in rows], normalized_lemma)
    kept = [row for row, k in zip(rows, keep, strict=True) if k]
    # Redirect substitution BEFORE the pool cap (matching lookup_many) so a
    # resolved canonical entry can't be truncated by its own pointer row.
    projected = _substitute_redirect_rows(conn, [(row[0], row[1], row[2]) for row in kept])
    return projected[:_LOOKUP_LIMIT]  # type: ignore[return-value]


def lookup_with_rules(conn: sqlite3.Connection, word: str) -> list[tuple[str, str, int | None, str]]:
    """Return (content, tags, sequence, rules) rows matching ``word`` by term or
    folded reading, ranked like :func:`lookup` (no reading boost).

    The lookup-miss fallback (plan item 5.2) probes deinflection/variant
    candidates through this so it can POS-check each row's ``rules`` before
    rendering. A NULL/absent ``rules`` column normalises to ``""`` (accept
    unconditionally at the caller). Katakana folding matches ``lookup``: a
    katakana candidate still matches a kanji headword's hiragana-folded reading.
    """
    word = unicodedata.normalize("NFC", word)
    folded_word = katakana_to_hiragana(word)
    rows = conn.execute(_LOOKUP_RULES_SQL, (word, folded_word, word)).fetchall()
    # rows: (content, tags, sequence, rules, term). Render-side, so scope
    # homographs (Rule A/B) then apply the pool cap, mirroring ``lookup``.
    keep = _homograph_keep_mask(word, [(row[4], row[0]) for row in rows])
    kept = [row for row, k in zip(rows, keep, strict=True) if k]
    projected = _substitute_redirect_rows(
        conn,
        [(row[0], row[1], row[2], row[3] if row[3] is not None else "") for row in kept],
        with_rules=True,
    )
    return projected[:_LOOKUP_LIMIT]  # type: ignore[return-value]


# sqlite's default SQLITE_MAX_VARIABLE_NUMBER is 999. attest_detail's
# term-only arm (include_readings=False) binds each word ONCE (a single
# ``term IN (...)`` clause), so a chunk of _BIND_CHUNK words stays
# comfortably under the cap even with room to spare. The reading-inclusive
# arm binds each word twice (term IN + reading IN) and uses the smaller
# _ATTEST_READING_CHUNK instead — see that constant.
_BIND_CHUNK = 450

# lookup_many binds 3 placeholders per word (req_idx, term, folded term) into
# ONE UNION ALL statement, so the 999-variable cap is not the binding
# constraint here. The chunk stays deliberately small because each word's
# subquery is UNBOUNDED (see lookup_many): fewer words per round trip is what
# bounds how many hyper-common-reading collisions can compound into one
# fetchall() of full-content rows.
_LOOKUP_MANY_CHUNK = 150

# attest_detail must see EVERY attesting row per word (commonness/quality
# verdicts need completeness, unlike lookup_many's _LOOKUP_LIMIT display
# pool). Batching fewer words per round trip bounds how many separate
# common-reading collisions can compound into one fetchall() — the term-only
# arm keeps _BIND_CHUNK since an exact headword repeating at that scale is far
# rarer.
_ATTEST_READING_CHUNK = 100


def lookup_many(
    conn: sqlite3.Connection,
    pairs: list[tuple[str, str | None]],
    scope_homographs: bool = True,
    lemmas: dict[str, str] | None = None,
) -> dict[str, list[tuple[str, str, int | None]]]:
    """Batch variant of :func:`lookup`.

    ``pairs`` is a list of ``(word, reading | None)`` — each word's contextual
    reading boosts *that word's own bucket* (``None`` = wildcard, no boost).
    Runs ONE query per chunk — a ``UNION ALL`` of one ``_LOOKUP_SQL``-shaped
    subquery per word — instead of one query per word, then reproduces
    ``_LOOKUP_SQL``'s ordering and the pool cap in Python so each per-word
    result is byte-identical, row-for-row, to ``lookup(conn, word, reading)``
    on every input. The per-word fetch is unbounded on purpose: see the
    ``_LOOKUP_LIMIT`` comment for why a SQL row cap cannot preserve that
    parity.

    ``lemmas`` optionally maps a requested word to its token's UniDic lemma,
    threaded into the Rule A′ homograph scope per word (see
    :func:`_homograph_keep_mask`); inert when ``scope_homographs`` is False.

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

    normalized_by_word = {word: unicodedata.normalize("NFC", word) for word, _ in unique_pairs}

    # Per-word folded boost reading (hiragana-folded to match stored readings);
    # None keeps the wildcard (no-boost) ordering for that word.
    boost_by_word: dict[str, str | None] = {w: _fold_reading(r) for w, r in unique_pairs}
    unique_words = [w for w, _ in unique_pairs]

    for start in range(0, len(unique_words), _LOOKUP_MANY_CHUNK):
        chunk = unique_words[start : start + _LOOKUP_MANY_CHUNK]

        # One _LOOKUP_SQL-shaped subquery per word, tagged with its position in
        # ``chunk`` (req_idx) so each fetched row can be routed back to its own
        # word without any reverse-mapping: a row satisfies exactly the
        # subquery that fetched it, term match or reading match, collapsed to
        # one row per word by SQL's own ``OR`` (matching _LOOKUP_SQL's
        # "term=? OR reading=?" semantics — a dual-match row still appears
        # once). The fetch is deliberately unbounded, exactly as ``lookup``'s
        # is: a SQL row cap would truncate BEFORE homograph scoping and could
        # hide a survivor ranked past it. Ordering is reproduced in Python
        # below, so no ORDER BY is needed here either.
        subqueries: list[str] = []
        params: list[object] = []
        for idx, w in enumerate(chunk):
            normalized = normalized_by_word[w]
            # Readings are stored hiragana-folded, so the reading-match bind
            # must be the folded query word (touch point b) — a katakana
            # requested word still fetches the row whose folded reading it
            # matches.
            folded_term = katakana_to_hiragana(normalized)
            subqueries.append(
                "SELECT ? AS req_idx, id, term, reading, content, tags, score, sequence FROM entries "
                "WHERE term = ? OR reading = ?"
            )
            params.extend([idx, normalized, folded_term])
        sql = " UNION ALL ".join(subqueries)
        rows = conn.execute(sql, params).fetchall()

        # Bucket each fetched row under its own req_idx-tagged word. Each entry
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
        buckets: dict[str, list[tuple[int, int, tuple[int, int], tuple[int, int], int, str, str, str, int | None]]] = {
            w: [] for w in chunk
        }
        for req_idx, row_id, term, reading, content, tags, score, sequence in rows:
            w = chunk[req_idx]
            tags_val = tags if tags is not None else ""
            folded_reading = katakana_to_hiragana(reading) if reading is not None else None
            seq_key = _seq_key(sequence)
            score_key = _score_key(score)
            term_priority = 0 if term == normalized_by_word[w] else 1
            reading_priority = _reading_priority(folded_reading, boost_by_word[w])
            buckets[w].append(
                (term_priority, reading_priority, score_key, seq_key, row_id, term, content, tags_val, sequence)
            )

        pending: dict[str, list[tuple[str, str, int | None]]] = {}
        for w, entries in buckets.items():
            if scope_homographs:
                # Filter BEFORE sort/cap: order-independent per-row predicate, so
                # scoping then sorting equals ``lookup``'s scope-the-sorted-set.
                # e[5]=term, e[6]=content (see the entry tuple above).
                raw_lemma = (lemmas or {}).get(w)
                normalized_lemma = unicodedata.normalize("NFC", raw_lemma) if raw_lemma else None
                keep = _homograph_keep_mask(normalized_by_word[w], [(e[5], e[6]) for e in entries], normalized_lemma)
                entries = [e for e, k in zip(entries, keep, strict=True) if k]
            entries.sort(key=lambda e: (e[0], e[1], e[2], e[3], e[4]))
            pending[w] = [(content, tags, seq) for *_keys, content, tags, seq in entries]

        # Redirect substitution BEFORE the pool cap, sharing ONE target fetch
        # across the whole chunk (a table scan on pre-idx_sequence indexes must
        # not repeat per word). Per-word splice order matches ``lookup``'s, so
        # the lookup == lookup_many parity invariant survives.
        chunk_targets = list(
            dict.fromkeys(
                -row[2]
                for rows3 in pending.values()
                for row in rows3
                if row[2] is not None and _is_redirect_row(row[0], row[2])
            )
        )
        resolved_by_seq = _fetch_rows_for_sequences(conn, chunk_targets) if chunk_targets else {}
        for w, rows3 in pending.items():
            substituted = _substitute_redirect_rows(conn, rows3, resolved_by_seq)
            result[w] = substituted[:_LOOKUP_LIMIT]  # type: ignore[assignment]

    return result


# terms_exist binds each term ONCE (single-column IN), so the chunk can be
# larger than _BIND_CHUNK while staying under sqlite's default
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
    normalized = {term: unicodedata.normalize("NFC", term) for term in unique}
    requested_by_term: dict[str, list[str]] = {}
    for requested, term in normalized.items():
        requested_by_term.setdefault(term, []).append(requested)
    canonical_terms = list(requested_by_term)
    found: set[str] = set()
    for start in range(0, len(canonical_terms), _EXIST_CHUNK):
        chunk = canonical_terms[start : start + _EXIST_CHUNK]
        placeholders = ", ".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT DISTINCT term FROM entries WHERE term IN ({placeholders})",
            chunk,
        ).fetchall()
        for (term,) in rows:
            found.update(requested_by_term.get(term, ()))
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
    requested_by_term: dict[str, list[str]] = {}
    for requested in unique:
        requested_by_term.setdefault(unicodedata.normalize("NFC", requested), []).append(requested)
    canonical_terms = list(requested_by_term)
    found: dict[str, list[str]] = {}
    for start in range(0, len(canonical_terms), _EXIST_CHUNK):
        chunk = canonical_terms[start : start + _EXIST_CHUNK]
        placeholders = ", ".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT term, reading FROM entries WHERE term IN ({placeholders}) "
            "AND reading IS NOT NULL AND reading != '' ORDER BY score DESC",
            chunk,
        ).fetchall()
        for term, reading in rows:
            for requested in requested_by_term.get(term, ()):
                readings = found.setdefault(requested, [])
                if reading not in readings:
                    readings.append(reading)
    return found


def exact_term_sequences(
    conn: sqlite3.Connection,
    pairs: list[tuple[str, str | None]],
) -> dict[tuple[str, str], set[int]]:
    """Return dictionary sequences for exact ``(term, reading)`` pairs.

    Both columns must match after the normal katakana-to-hiragana reading fold.
    Reading-only lookup hits deliberately do not count: this probe identifies
    lexemes, so a query for ``いでる`` must not inherit ``出でる``'s sequence.
    Rows without a sequence cannot provide stable dictionary identity and are
    omitted.

    A redirect row (negative sequence AND the ``⟶`` arrow, see
    ``_is_redirect_row``) is folded to its canonical (positive) sequence: a kana
    redirect that carries a reading (e.g. あかーん) must share identity with the
    entry it points at, or the orthographic-alias dedup would treat ``-seq`` and
    ``+seq`` as two lexemes. A foreign dictionary's own negative-sequence rows
    (no arrow) are real content and pass through untouched, so ``-N`` and ``+N``
    stay distinct identities for those. Both sides of every identity comparison
    flow through this probe, so the fold is consistent.
    """
    normalized_pairs: list[tuple[str, str]] = []
    for term, reading in pairs:
        if not term or not reading:
            continue
        folded_reading = _fold_reading(reading)
        if folded_reading:
            normalized_pairs.append((unicodedata.normalize("NFC", term), folded_reading))
    normalized_pairs = list(dict.fromkeys(normalized_pairs))
    requested = set(normalized_pairs)
    terms = list(dict.fromkeys(term for term, _ in normalized_pairs))
    found: dict[tuple[str, str], set[int]] = {}

    for start in range(0, len(terms), _EXIST_CHUNK):
        chunk = terms[start : start + _EXIST_CHUNK]
        placeholders = ", ".join("?" for _ in chunk)
        rows = conn.execute(
            "SELECT DISTINCT term, reading, sequence, content FROM entries "
            f"WHERE term IN ({placeholders}) AND reading IS NOT NULL "
            "AND reading != '' AND sequence IS NOT NULL",
            chunk,
        ).fetchall()
        for term, reading, sequence, content in rows:
            folded_reading = _fold_reading(reading)
            if folded_reading is None:
                continue
            key = (term, folded_reading)
            if key in requested:
                seq = -sequence if _is_redirect_row(content, sequence) else sequence
                found.setdefault(key, set()).add(seq)

    return found


def attest_detail(conn: sqlite3.Connection, words: list[str], include_readings: bool) -> dict[str, list[AttestRow]]:
    """Per-word attesting rows for the commonness/quality probes (U10 infra).

    For each requested word returns the rows that attest it, each carrying its
    ``rules`` and ``tags`` columns:

    * term-exact rows (``entries.term == word``) — ``match_kind='term'``, ALWAYS.
    * kana reading rows (hiragana-folded ``entries.reading == fold(word)``) —
      ``match_kind='reading'``, only when ``include_readings``. Uses the SAME
      katakana→hiragana fold as :func:`lookup_many`'s reading arm (both the query
      word and the stored reading are folded; readings are stored pre-folded).

    A row matching a word by BOTH term and reading is emitted once, classified
    ``'term'`` (term wins, mirroring ``lookup_many``'s per-word collapse). Every
    requested word is present (``[]`` when unattested); duplicate words collapse
    to one key. Row order within a word is unspecified — this is a probe, not a
    render path; the provider unions into order-independent frozensets.
    """
    unique = list(dict.fromkeys(words))
    result: dict[str, list[AttestRow]] = {w: [] for w in unique}
    if not unique:
        return result

    normalized_by_word = {word: unicodedata.normalize("NFC", word) for word in unique}

    # The reading arm batches fewer words per round trip (see _ATTEST_READING_CHUNK):
    # a common kana reading can attest thousands of rows, and every word sharing
    # a chunk adds its own attesting rows to the SAME fetchall().
    chunk_size = _ATTEST_READING_CHUNK if include_readings else _BIND_CHUNK
    for start in range(0, len(unique), chunk_size):
        chunk = unique[start : start + chunk_size]
        normalized_chunk = [normalized_by_word[word] for word in chunk]
        term_reverse: dict[str, list[str]] = {}
        for requested, normalized in zip(chunk, normalized_chunk, strict=True):
            term_reverse.setdefault(normalized, []).append(requested)
        if include_readings:
            # Readings are stored hiragana-folded, so bind the folded query words
            # (touch point b) and map a reading hit back through the folded key —
            # a katakana requested word still attests via a kanji headword's
            # folded reading (mirrors lookup_many's reading_reverse).
            folded_chunk = [katakana_to_hiragana(w) for w in normalized_chunk]
            reading_reverse: dict[str, list[str]] = {}
            for w, wf in zip(chunk, folded_chunk, strict=True):
                reading_reverse.setdefault(wf, []).append(w)
            placeholders = ", ".join("?" for _ in chunk)
            sql = (
                "SELECT term, reading, rules, tags FROM entries "
                f"WHERE term IN ({placeholders}) OR reading IN ({placeholders})"
            )
            rows = conn.execute(sql, (*normalized_chunk, *folded_chunk)).fetchall()
            for term, reading, rules, tags in rows:
                rules_val = rules if rules is not None else ""
                tags_val = tags if tags is not None else ""
                folded_reading = katakana_to_hiragana(reading) if reading is not None else None
                # Term wins over reading for the same (row, word) pair.
                matched: dict[str, str] = {}
                for w in term_reverse.get(term, ()):
                    matched[w] = "term"
                if folded_reading is not None:
                    for w in reading_reverse.get(folded_reading, ()):
                        matched.setdefault(w, "reading")
                for w, kind in matched.items():
                    result[w].append(AttestRow(kind, rules_val, tags_val))
        else:
            placeholders = ", ".join("?" for _ in chunk)
            sql = f"SELECT term, rules, tags FROM entries WHERE term IN ({placeholders})"
            for term, rules, tags in conn.execute(sql, normalized_chunk).fetchall():
                for requested in term_reverse.get(term, ()):
                    result[requested].append(
                        AttestRow("term", rules if rules is not None else "", tags if tags is not None else "")
                    )
    return result


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
