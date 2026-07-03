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
    terms_exist as storage_terms_exist,
)

logger = logging.getLogger(__name__)


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

    def lookup_many(self, words: list[str]) -> dict[str, str | None]:
        """Batch lookup. Runs one IN-clause query per dictionary (chunked),
        then renders each word's HTML through the SAME ``_render`` path as
        :meth:`lookup`, so single and batch results are byte-identical."""
        if self._conn is None:
            return dict.fromkeys(words)
        try:
            rows_by_word = storage_lookup_many(self._conn, words)
        except sqlite3.DatabaseError as e:
            logger.warning(
                "Dictionary '%s' (%s) raised DatabaseError during lookup_many; treating as all-miss: %s",
                self.dict_id,
                self._db_path,
                e,
            )
            return dict.fromkeys(words)
        # storage_lookup_many keys by unique requested words; re-expand to every
        # requested word (preserving duplicates) for caller convenience.
        return {w: self._render(rows_by_word.get(w, [])) for w in words}

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

    def _render(self, rows: list[tuple[str, str]]) -> str | None:
        """Assemble Lapis-shape HTML from (content, tags) rows. Returns None
        when there are no rows. Shared by lookup and lookup_many to guarantee
        byte-identical output.

        Deduplication (OVH-026): some dictionaries double-key the same entry —
        once under a kanji term with a kana reading, and again under the kana
        term alone. Both rows carry identical ``content``. We keep the first-seen
        row for each unique content blob and still UNION the tags from all
        duplicate rows, so no information is lost.

        Tag rendering (schema v3): a unioned tag with a ``tags``-table row is
        emitted as a hover chip (``<span class="gloss-tag" data-category=…
        title=notes>name</span>``), sorted by ``(ord, -score, name)`` — Yomitan
        ``_mergeSimilarTags`` order. Tags without a row keep the pre-v3 italic
        token line, so a dict with no tag metadata renders byte-identically.
        """
        if not rows:
            return None

        # Build tag union preserving first-seen order across all hits,
        # and deduplicate rows with identical content (keep first seen).
        ordered_tags: list[str] = []
        seen_tags: set[str] = set()
        seen_content: set[str] = set()
        unique_rows: list[tuple[str, str]] = []
        for content, tags in rows:
            # Always union in the tags, even from duplicate-content rows.
            if tags:
                for tag in tags.split(" "):
                    if tag and tag not in seen_tags:
                        seen_tags.add(tag)
                        ordered_tags.append(tag)
            # Only keep the first occurrence of each distinct content blob.
            if content not in seen_content:
                seen_content.add(content)
                unique_rows.append((content, tags))

        # Merge gloss-item blobs by simple concatenation (renderer emits <li class="gloss-item">…</li>).
        merged = "".join(content for content, _tags in unique_rows)

        # Count gloss-items. Use prefix without closing '>' so future class additions still match.
        item_count = merged.count('<li class="gloss-item"')

        dict_label = self._display_name
        escaped_attr = html.escape(dict_label, quote=True)

        # Partition unioned tags into chip-eligible (has a tags-table row) and
        # italic-fallback (no row). Chips sort by (ord, -score, name); fallback
        # tags keep first-seen order.
        tag_meta = self._tag_meta()
        chip_metas = [tag_meta[t] for t in ordered_tags if t in tag_meta]
        chip_metas.sort(key=lambda m: (m.ord, -m.score, m.name))
        chips = "".join(
            f'<span class="gloss-tag" data-category="{html.escape(m.category, quote=True)}"'
            f' title="{html.escape(m.notes, quote=True)}">{html.escape(m.name)}</span>'
            for m in chip_metas
        )
        fallback_tags = [t for t in ordered_tags if t not in tag_meta]
        italic_parts = fallback_tags + [dict_label]
        escaped_italic = html.escape(", ".join(italic_parts), quote=True)

        return (
            '<div class="yomitan-glossary">'
            '<ol data-count="1">'
            f'<li data-dictionary="{escaped_attr}">'
            f"{chips}"
            f"<i>({escaped_italic})</i>"
            f'<ul class="gloss-list" data-count="{item_count}">{merged}</ul>'
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
