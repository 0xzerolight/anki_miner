"""Render the per-source frequency breakdown to HTML for the Anki card.

The breakdown is a plain bullet list of ``Source: value`` rows in chain order,
matching the additive aggregation produced by
:meth:`MultiFrequencyService.lookup_all`. Each row shows the source's
``display_value`` when present (the human string Yomitan preserves, e.g.
"1099/72000"), else the bare integer rank — mirroring Yomitan's
``displayValue ?? frequency`` card rule (anki-note-data-creator.js). Source names
and display values are HTML-escaped since both originate from user-imported
dictionary metadata; ranks are integers (no escaping needed).
"""

from __future__ import annotations

import html
import logging

logger = logging.getLogger(__name__)


def render_frequency_html(sources: list[tuple[str, int, str | None]]) -> str:
    """Render ``(name, rank, display_value)`` rows as ``<ul><li>name: value</li>…</ul>``.

    ``value`` is the escaped ``display_value`` when set, else the bare ``rank``.
    Source names and display values are HTML-escaped. Returns ``""`` for an empty
    list.
    """
    if not sources:
        return ""
    items = "".join(
        f"<li>{html.escape(name)}: {html.escape(display_value) if display_value else rank}</li>"
        for name, rank, display_value in sources
    )
    return f"<ul>{items}</ul>"
