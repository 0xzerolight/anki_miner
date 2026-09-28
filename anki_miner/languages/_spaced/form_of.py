"""Readers for the dictionary's own form-of rows (spec R36 ``form_lookup``).

A Wiktionary-derived dictionary (``wty-*``) keys every inflected form it knows as a ``non-lemma``
row whose glossary names the lemma it belongs to. ``service_factory`` wires those rows into every
parser as R36's ``form_lookup`` (``DefinitionService.offline_term_rows``), and a ``token_post_pass``
is their only reader.

**A form row's target is parsed out of the RENDERED content, not out of raw JSON.** The Yomitan
importer stores ``render_glossary_entry(...)`` output in the ``content`` column
(``yomitan_importer.py``), so a single-target row arrives as
``<li class="gloss-item"><div class="gloss-content">LEMMA</div></li>`` and a multi-target row wraps
its targets in ``<li class="gloss-sc-li">``.
"""

from __future__ import annotations

import html
import re

__all__ = ["form_targets", "is_lemma_row", "rendered_text"]

_GLOSS_CONTENT_RE = re.compile(r'<div class="gloss-content">(.*?)</div>', re.S)
_GLOSS_ITEM_RE = re.compile(r'<li class="gloss-sc-li">(.*?)</li>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_NON_LEMMA = "non-lemma"


def rendered_text(markup: str) -> str:
    """The text of a piece of rendered glossary HTML: tags dropped, entities decoded, trimmed."""
    return html.unescape(_TAG_RE.sub("", markup)).strip()


def is_lemma_row(tags: str) -> bool:
    """A row the dictionary files as a headword rather than as an inflected form."""
    return _NON_LEMMA not in tags.split(" ")


def form_targets(content: str) -> list[str]:
    """The lemmas a form row's rendered content names, in order (spec F.2, measured shapes)."""
    found: list[str] = []
    for block in _GLOSS_CONTENT_RE.findall(content or ""):
        items = _GLOSS_ITEM_RE.findall(block)
        for item in items or [block]:
            target = rendered_text(item)
            if target:
                found.append(target)
    return found
