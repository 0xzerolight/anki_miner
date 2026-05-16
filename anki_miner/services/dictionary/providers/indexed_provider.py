"""SQLite-backed dictionary provider implementing DictionaryProvider Protocol."""

from __future__ import annotations

import contextlib
import html
import logging
import sqlite3
from pathlib import Path

from anki_miner.services.dictionary.storage import (
    SCHEMA_VERSION,
    open_readonly,
    read_meta,
)
from anki_miner.services.dictionary.storage import (
    lookup as storage_lookup,
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

    @property
    def name(self) -> str:
        return self._display_name

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
        return True

    def lookup(self, word: str) -> str | None:
        if self._conn is None:
            return None
        rows = storage_lookup(self._conn, word)
        if not rows:
            return None

        # Build tag union preserving first-seen order across all hits.
        ordered_tags: list[str] = []
        seen_tags: set[str] = set()
        for _content, tags in rows:
            if not tags:
                continue
            for tag in tags.split(" "):
                if tag and tag not in seen_tags:
                    seen_tags.add(tag)
                    ordered_tags.append(tag)

        # Merge gloss-item blobs by simple concatenation (renderer emits <li class="gloss-item">…</li>).
        merged = "".join(content for content, _tags in rows)

        # Count gloss-items. Use prefix without closing '>' so future class additions still match.
        item_count = merged.count('<li class="gloss-item"')

        dict_label = self._display_name
        escaped_attr = html.escape(dict_label, quote=True)
        italic_parts = ordered_tags + [dict_label]
        escaped_italic = html.escape(", ".join(italic_parts), quote=True)

        return (
            '<div class="yomitan-glossary">'
            '<ol data-count="1">'
            f'<li data-dictionary="{escaped_attr}">'
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
