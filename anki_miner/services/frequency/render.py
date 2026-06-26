"""Render the per-source frequency breakdown to HTML for the Anki card.

The breakdown is a plain bullet list of ``Source: rank`` rows in chain order,
matching the additive aggregation produced by
:meth:`MultiFrequencyService.lookup_all`. Ranks are integers (no escaping
needed); source names are HTML-escaped since they originate from user-imported
dictionary metadata.
"""

from __future__ import annotations

import html
import logging

logger = logging.getLogger(__name__)


def render_frequency_html(sources: list[tuple[str, int]]) -> str:
    """Render ``(name, rank)`` pairs as ``<ul><li>name: rank</li>…</ul>``.

    Source names are HTML-escaped. Returns ``""`` for an empty list.
    """
    if not sources:
        return ""
    items = "".join(f"<li>{html.escape(name)}: {rank}</li>" for name, rank in sources)
    return f"<ul>{items}</ul>"
