"""SQLite-backed dictionary provider implementing DictionaryProvider Protocol."""

from __future__ import annotations

import contextlib
import html
import logging
import sqlite3
from pathlib import Path

from anki_miner.services.dictionary.dict_css_scope import scope_dict_css
from anki_miner.services.dictionary.storage import (
    SCHEMA_VERSION,
    TagMeta,
    open_readonly,
    read_meta,
    read_tags,
)
from anki_miner.services.dictionary.storage import (
    lookup as storage_lookup,
)
from anki_miner.services.dictionary.storage import (
    lookup_many as storage_lookup_many,
)
from anki_miner.services.dictionary.storage import (
    lookup_with_rules as storage_lookup_with_rules,
)
from anki_miner.services.dictionary.storage import (
    terms_exist as storage_terms_exist,
)
from anki_miner.services.dictionary.storage import (
    terms_readings as storage_terms_readings,
)

logger = logging.getLogger(__name__)

# Rendered senses shown per dictionary hit, applied AFTER content-dedup and
# sequence grouping (dedup-before-cap, plan item 5.1). Storage fetches a larger
# candidate pool (storage._LOOKUP_LIMIT) so duplicate-content rows can't consume
# a display slot before dedup runs.
_DISPLAY_LIMIT = 5


class IndexedDictProvider:
    """SQLite-backed implementation of the DictionaryProvider Protocol.

    Threading: the underlying read-only SQLite connection is opened with
    check_same_thread=False, so a single provider instance is safe to share
    across threads for lookups. sqlite3 serializes concurrent reads
    internally via the GIL + sqlite library mutex.
    """

    def __init__(self, dict_id: str, db_path: Path, display_name: str | None = None):
        self.dict_id = dict_id
        self._db_path = db_path
        self._display_name = display_name or dict_id
        self._conn: sqlite3.Connection | None = None
        # Lazy per-provider tag-metadata cache (Yomitan _tagCache analog):
        # ``{tag_name: TagMeta}`` from the schema-v3 ``tags`` table, populated on
        # first render. ``None`` until then; ``{}`` for a dict that shipped no
        # tag_bank / legacy tagMeta (every tag then renders in the italic
        # fallback line, preserving pre-v3 output).
        self._tag_cache: dict[str, TagMeta] | None = None
        # This dictionary's own styles.css, scoped to its glossary markup
        # (Issue #87), as a bare CSS string (no <style> wrapper). Empty unless
        # the dict shipped a styles.css that survived scoping; computed in
        # load(). Concatenated by collect_dictionary_css and emitted in each
        # card's per-card <style> block by build_card_style_block (assembled at
        # the EpisodeProcessor._phase5_create seam).
        self._scoped_css = ""

    @property
    def name(self) -> str:
        return self._display_name

    @property
    def dictionary_css(self) -> str:
        """This dictionary's scoped styles.css (bare CSS, no <style> wrapper).

        Empty for JMdict, online providers, and dicts imported before styles.css
        capture. Concatenated by ``collect_dictionary_css`` into each card's
        per-card ``<style>`` block; only valid after a successful ``load()``.
        """
        return self._scoped_css

    @property
    def is_online(self) -> bool:
        return False

    def is_available(self) -> bool:
        return self._conn is not None

    def load(self) -> bool:
        if self._conn is not None:
            return True
        if not self._db_path.exists():
            logger.warning("Dictionary index missing: %s", self._db_path)
            return False
        try:
            meta = read_meta(self._db_path)
        except sqlite3.DatabaseError as e:
            logger.warning("Dictionary index unreadable (%s): %s", self._db_path, e)
            return False

        try:
            version = int(meta.get("schema_version", "0"))
        except ValueError:
            version = 0
        if version != SCHEMA_VERSION:
            logger.warning(
                "Dictionary %s has schema_version=%s, expected %s — needs reimport",
                self.dict_id,
                version,
                SCHEMA_VERSION,
            )
            return False

        try:
            self._conn = open_readonly(self._db_path)
        except sqlite3.DatabaseError as e:
            logger.warning("Failed to open %s: %s", self._db_path, e)
            return False

        # Scope the dict's own styles.css (Issue #87) once. Stored bare (no
        # <style> wrapper) and exposed via `dictionary_css`; collect_dictionary_css
        # concatenates it into each card's per-card <style> block. Absent for
        # JMdict and for dicts imported before styles.css capture.
        self._scoped_css = scope_dict_css(meta.get("styles_css", ""), self._display_name)
        return True

    def lookup(self, word: str) -> str | None:
        if self._conn is None:
            return None
        try:
            return self._render(storage_lookup(self._conn, word))
        except sqlite3.DatabaseError as e:
            logger.warning(
                "Dictionary '%s' (%s) raised DatabaseError during lookup; treating as miss: %s",
                self.dict_id,
                self._db_path,
                e,
            )
            return None

    def lookup_many(self, pairs: list[tuple[str, str | None]]) -> dict[str, str | None]:
        """Batch lookup over ``(word, reading | None)`` pairs. Each word's
        contextual reading boosts that word's own ranking (``None`` = wildcard).
        Runs one IN-clause query per dictionary (chunked), then renders each
        word's HTML through the SAME ``_render`` path as :meth:`lookup`, so single
        and batch results are byte-identical. Keyed by word (duplicate words
        collapse)."""
        if self._conn is None:
            return {w: None for w, _ in pairs}
        try:
            rows_by_word = storage_lookup_many(self._conn, pairs)
        except sqlite3.DatabaseError as e:
            logger.warning(
                "Dictionary '%s' (%s) raised DatabaseError during lookup_many; treating as all-miss: %s",
                self.dict_id,
                self._db_path,
                e,
            )
            return {w: None for w, _ in pairs}
        # storage_lookup_many keys by unique requested words; re-expand to every
        # requested word (preserving duplicates) for caller convenience.
        return {w: self._render(rows_by_word.get(w, [])) for w, _ in pairs}

    def lookup_fallback(self, word: str, conditions: int) -> str | None:
        """Rules-validated lookup for a deinflection/variant fallback candidate.

        Ported from Yomitan ``Translator._matchEntriesToDeinflections``
        (ext/js/language/translator.js, upstream e2ed450): each candidate row is
        kept only when its stored ``rules`` (mapped to condition flags) is
        compatible with the hypothesis ``conditions`` — upstream's
        ``partsOfSpeechFilter`` POS check. ``conditions`` is the deinflection
        hypothesis's condition bitmask (0 for a pure spelling/kana variant, which
        then passes unconditionally). A row with EMPTY ``rules`` (older imports,
        rule-less dicts) is accepted unconditionally so the column degrades
        gracefully. Surviving rows render through the SAME ``_render`` path as
        :meth:`lookup`, so a validated fallback hit is byte-identical to a direct
        hit. Optional method (probed via ``getattr`` like ``lookup_many`` /
        ``has_terms``); never raises — a corrupt index degrades to a miss.
        """
        if self._conn is None:
            return None
        # Lazy import: keeps the deinflection rule table off this module's import
        # path (mirrors find_highlight_end's lazy pull) and avoids a cycle.
        from anki_miner.services.deinflection import condition_flags_from_rules, conditions_match

        try:
            rows = storage_lookup_with_rules(self._conn, word)
        except sqlite3.DatabaseError as e:
            logger.warning(
                "Dictionary '%s' (%s) raised DatabaseError during lookup_fallback; treating as miss: %s",
                self.dict_id,
                self._db_path,
                e,
            )
            return None
        kept: list[tuple[str, str, int | None]] = [
            (content, tags, sequence)
            for content, tags, sequence, rules in rows
            if not rules or conditions_match(conditions, condition_flags_from_rules(rules))
        ]
        return self._render(kept)

    def has_terms(self, terms: list[str]) -> set[str]:
        """Batch exact-term existence probe (compound matching).

        Returns the subset of ``terms`` that exist as headwords (``entries.term``)
        in this dictionary. Reading-only matches do not count. Never raises:
        unavailable or corrupt index degrades to an empty set (all-miss).
        """
        if self._conn is None:
            return set()
        try:
            return storage_terms_exist(self._conn, terms)
        except sqlite3.DatabaseError as e:
            logger.warning(
                "Dictionary '%s' (%s) raised DatabaseError during has_terms; treating as all-miss: %s",
                self.dict_id,
                self._db_path,
                e,
            )
            return set()

    def terms_readings(self, terms: list[str]) -> dict[str, list[str]]:
        """Batch attested-readings probe (merged-compound reading attestation).

        Maps each of ``terms`` that exists as a headword with a non-empty
        reading to its readings, best entry first (``entries.score`` DESC,
        hiragana-folded as stored). Mirrors :meth:`has_terms`: never raises;
        unavailable or corrupt index degrades to an empty map (all-miss).
        """
        if self._conn is None:
            return {}
        try:
            return storage_terms_readings(self._conn, terms)
        except sqlite3.DatabaseError as e:
            logger.warning(
                "Dictionary '%s' (%s) raised DatabaseError during terms_readings; treating as all-miss: %s",
                self.dict_id,
                self._db_path,
                e,
            )
            return {}

    def _tag_meta(self) -> dict[str, TagMeta]:
        """Lazily load and cache this dictionary's ``tags`` table.

        Yomitan ``_tagCache`` analog: the per-dictionary tag-metadata map is
        read once on first render and reused. A read failure (or an unloaded
        connection) degrades to an empty map so every tag simply falls back to
        the italic token line — never raising into a lookup.
        """
        if self._tag_cache is not None:
            return self._tag_cache
        if self._conn is None:
            return {}
        try:
            self._tag_cache = read_tags(self._conn)
        except sqlite3.DatabaseError as e:
            logger.warning(
                "Dictionary '%s' (%s) raised DatabaseError reading tags; italic fallback: %s",
                self.dict_id,
                self._db_path,
                e,
            )
            self._tag_cache = {}
        return self._tag_cache

    def _render(self, rows: list[tuple[str, str, int | None]]) -> str | None:
        """Assemble Lapis-shape HTML from (content, tags, sequence) rows. Returns
        None when there are no rows. Shared by lookup and lookup_many to guarantee
        byte-identical output.

        Sequence grouping. Ported from Yomitan ``Translator._getRelatedDictionary
        Entries`` + ``_createGroupedDictionaryEntry`` (ext/js/language/translator.js,
        upstream e2ed450): rows that share a dictionary ``sequence`` belong to one
        lexeme and are rendered as ONE sub-block with its own tag line; unrelated
        lexemes (different sequence) get their own sub-block so tags are no longer
        unioned across them. A row with no sequence (``None``) is its own group.
        All sub-blocks stay inside the single ``<li data-dictionary>`` envelope so
        Lapis/Senren dictionary-toggle CSS is unaffected.

        Deduplication before the cap (OVH-026, plan item 5.1): some dictionaries
        double-key the same entry — once under a kanji term with a kana reading,
        and again under the kana term alone. Both rows carry identical ``content``.
        We keep the first-seen row for each unique content blob (unioning the tags
        from every duplicate so nothing is lost) and only THEN apply the display
        cap, so duplicate rows can no longer consume a display slot ahead of a
        real sense — storage over-fetches a pool for exactly this reason.

        Tag rendering (schema v3): a unioned tag with a ``tags``-table row is
        emitted as a hover chip (``<span class="gloss-tag" data-category=…
        title=notes>name</span>``), sorted by ``(ord, -score, name)`` — Yomitan
        ``_mergeSimilarTags`` order. Tags without a row keep the italic token line.
        A single-group hit (the common case: one sequence, or all NULL) renders
        byte-identically to the pre-5.1 single-block output.
        """
        if not rows:
            return None

        # Content-dedup into ordered "senses": first-seen content wins, tags from
        # every duplicate content row union into that sense, and the first-seen
        # sequence is the sense's group key. ``sense_tags`` preserves first-seen
        # tag order per sense.
        sense_order: list[str] = []
        sense_seq: dict[str, int | None] = {}
        sense_tags: dict[str, list[str]] = {}
        sense_tags_seen: dict[str, set[str]] = {}
        for content, tags, sequence in rows:
            if content not in sense_seq:
                sense_order.append(content)
                sense_seq[content] = sequence
                sense_tags[content] = []
                sense_tags_seen[content] = set()
            if tags:
                seen = sense_tags_seen[content]
                ordered = sense_tags[content]
                for tag in tags.split(" "):
                    if tag and tag not in seen:
                        seen.add(tag)
                        ordered.append(tag)

        # Group senses by sequence (None = its own group), preserving first-seen
        # group order and within-group sense order.
        groups: list[list[str]] = []
        group_index_by_seq: dict[int, int] = {}
        for content in sense_order:
            seq = sense_seq[content]
            if seq is None:
                groups.append([content])
                continue
            idx = group_index_by_seq.get(seq)
            if idx is None:
                group_index_by_seq[seq] = len(groups)
                groups.append([content])
            else:
                groups[idx].append(content)

        # Apply the display cap AFTER dedup + grouping: fill groups in order until
        # _DISPLAY_LIMIT senses are shown, truncating the crossing group.
        budget = _DISPLAY_LIMIT
        capped_groups: list[list[str]] = []
        for group in groups:
            if budget <= 0:
                break
            kept = group[:budget]
            budget -= len(kept)
            capped_groups.append(kept)

        dict_label = self._display_name
        escaped_attr = html.escape(dict_label, quote=True)
        tag_meta = self._tag_meta()

        # One sub-block per group: its own chips + italic tag line + gloss-list.
        blocks: list[str] = []
        for group in capped_groups:
            group_tags: list[str] = []
            group_tags_seen: set[str] = set()
            for content in group:
                for tag in sense_tags[content]:
                    if tag not in group_tags_seen:
                        group_tags_seen.add(tag)
                        group_tags.append(tag)

            merged = "".join(group)
            item_count = merged.count('<li class="gloss-item"')

            chip_metas = [tag_meta[t] for t in group_tags if t in tag_meta]
            chip_metas.sort(key=lambda m: (m.ord, -m.score, m.name))
            chips = "".join(
                f'<span class="gloss-tag" data-category="{html.escape(m.category, quote=True)}"'
                f' title="{html.escape(m.notes, quote=True)}">{html.escape(m.name)}</span>'
                for m in chip_metas
            )
            fallback_tags = [t for t in group_tags if t not in tag_meta]
            escaped_italic = html.escape(", ".join(fallback_tags + [dict_label]), quote=True)

            blocks.append(
                f"{chips}"
                f"<i>({escaped_italic})</i>"
                f'<ul class="gloss-list" data-count="{item_count}">{merged}</ul>'
            )

        # data-has-styles gates the base sheet's data-sc-* gap-fillers OFF for
        # dictionaries that ship a usable styles.css (glossary.css keys every
        # gap-filler on `li[data-dictionary]:not([data-has-styles])`), so the
        # dictionary's own scoped CSS governs its structured content — Yomitan
        # parity. Accepted limitations: (a) non-empty scoped CSS != complete
        # coverage — a partially sanitized or partially authored styles.css
        # still stamps, so any hook it doesn't style renders bare (Yomitan
        # gives such a dict no fallback either); (b) a pathological title
        # containing </>/control chars has those stripped from its CSS scope
        # selector (pre-existing css_string_escape fail-safe), so its own
        # styles.css never matches its markup — such an entry is stamped and
        # renders fully unstyled, consistently between fresh render and
        # restyle. Real titles never trip it.
        stamp = ' data-has-styles=""' if self._scoped_css else ""
        return (
            '<div class="yomitan-glossary">'
            '<ol data-count="1">'
            f'<li data-dictionary="{escaped_attr}"{stamp}>'
            f"{''.join(blocks)}"
            "</li>"
            "</ol>"
            "</div>"
        )

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self.close()
