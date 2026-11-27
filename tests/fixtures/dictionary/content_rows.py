"""Read dictionary rows with ``entries.content`` decoded, as the storage readers do."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from anki_miner.services.dictionary.storage import DictionaryConnection


def content_rows(conn: DictionaryConnection, sql: str, params: Sequence[object] = ()) -> list[tuple[Any, ...]]:
    """Every row of ``sql``, its ``content`` column decoded with the connection's codec."""
    cursor = conn.execute(sql, params)
    column = [description[0] for description in cursor.description].index("content")
    return [(*row[:column], conn.codec.decode(row[column]), *row[column + 1 :]) for row in cursor.fetchall()]


def content_row(conn: DictionaryConnection, sql: str, params: Sequence[object] = ()) -> tuple[Any, ...] | None:
    """The first row of :func:`content_rows`, or ``None``."""
    rows = content_rows(conn, sql, params)
    return rows[0] if rows else None
