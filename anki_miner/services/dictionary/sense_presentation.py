"""Write-time presentation of one stored dictionary row as a clean sense body.

Rows are rendered to HTML once at import (``yomitan_renderer.render_glossary_entry``)
and stored. This module reshapes one stored row for the card WITHOUT re-parsing it:
anchored regexes over the renderer's own shapes pick out pieces, and every fragment
it emits is a slice of the stored string. No HTML parser, no escape/unescape, no
re-serialization — stored escapes, media paths and links come out byte-identical.

Glosses are always a bullet list (``ol.gloss-glosses``), even a sense's only one;
usage notes are a muted block under it. A row is split into glosses only when it is
nothing but lists this module recognises (the renderer's synthesized members list,
or JMdict-style keyed lists). A row with no block content (``_BLOCK_TAGS``) is one
gloss: what the renderer emits for a one-member entry. Anything else is authored
structure and is returned verbatim.
"""

from __future__ import annotations

import re

# The gloss list carries NO ``data-sc-content``: Lapis minimal mode hides every
# ``[data-sc-content]`` whose value is not ``glossary``.
GLOSSES_OPEN = '<ol class="gloss-glosses"'

# The attributes of the renderer's synthesized members list, verbatim
# (``yomitan_renderer.render_glossary_entry``).
SYNTHESIZED_LIST_ATTRS = ' style="list-style-type: circle"'

_GLOSS_KEYS = frozenset({"glossary", "infoGlossary"})
_NOTE_KEY = "notes"
_DROPPED_KEYS = frozenset({"references", "antonyms", "sourceLanguages"})
_RECOGNISED_KEYS = _GLOSS_KEYS | {_NOTE_KEY} | _DROPPED_KEYS

# A recognised list never holds block content, and a simple one-gloss row has none;
# a row or list that does is authored structure.
_BLOCK_TAGS = ("<ul", "<ol", "<div", "<table")

_ROW_ITEM_RE = re.compile(r'<li class="gloss-item"[^>]*>(.*)</li>', re.DOTALL)
_ROW_CONTENT_RE = re.compile(r'<div class="gloss-content">(.*)</div>', re.DOTALL)

# A JMdict XML row: a bare ``<ol>`` whose items are senses. The tempered item body
# cannot run across ``</li>``, which keeps the match unambiguous (linear, no
# backtracking blow-up) and stops one item swallowing the next.
_XML_SENSES_RE = re.compile(r"<ol>((?:<li>(?:(?!</li>).)*</li>)+)</ol>", re.DOTALL)
_XML_SENSE_RE = re.compile(r"<li>(.*?)</li>", re.DOTALL)

_LIST_RE = re.compile(r'<ul class="gloss-sc-ul"([^>]*)>(.*?)</ul>', re.DOTALL)
_LIST_KEY_RE = re.compile(r'\sdata-sc-content="([^"]*)"')
_LANG_RE = re.compile(r'\slang="[^"]*"')
_LIST_ITEM_RE = re.compile(r'<li class="gloss-sc-li"[^>]*>(.*?)</li>', re.DOTALL)


def _unwrap(row_html: str) -> str:
    """The row's content without its ``<li class="gloss-item">``/``gloss-content`` wrappers."""
    inner = row_html.strip()
    item = _ROW_ITEM_RE.fullmatch(inner)
    if item:
        inner = item.group(1)
        content = _ROW_CONTENT_RE.fullmatch(inner)
        if content:
            inner = content.group(1)
    return inner.strip()


def _list_key(attrs: str) -> str | None:
    """The recognised role of a ``gloss-sc-ul`` from its attributes, or None for an authored list."""
    if attrs == SYNTHESIZED_LIST_ATTRS:
        return "glossary"
    key = _LIST_KEY_RE.search(attrs)
    if key and key.group(1) in _RECOGNISED_KEYS:
        return key.group(1)
    return None


def _list_items(body: str) -> list[str] | None:
    """The item bodies, or None unless ``body`` is only whitespace-separated flat ``gloss-sc-li`` items.

    Loose text or a ``span`` child sits directly in a structured ``ul``; a split would
    drop it, so such a list is not recognised and the row goes out verbatim.
    """
    items: list[str] = []
    pos = 0
    for match in _LIST_ITEM_RE.finditer(body):
        item = match.group(1)
        if body[pos : match.start()].strip() or "<li" in item:
            return None
        items.append(item)
        pos = match.end()
    if body[pos:].strip():
        return None
    return items


def _recognised_lists(inner: str) -> list[tuple[str, str, list[str]]] | None:
    """``(key, attrs, items)`` per list, or None unless ``inner`` is only recognised lists."""
    lists: list[tuple[str, str, list[str]]] = []
    pos = 0
    for match in _LIST_RE.finditer(inner):
        attrs, body = match.group(1), match.group(2)
        key = _list_key(attrs)
        if inner[pos : match.start()].strip() or key is None or any(tag in body for tag in _BLOCK_TAGS):
            return None
        items = _list_items(body)
        if items is None:
            return None
        lists.append((key, attrs, items))
        pos = match.end()
    if inner[pos:].strip():
        return None
    return lists


def _gloss_list(glosses: list[str], lang: str = "") -> str:
    """The glosses as a ``gloss-glosses`` bullet list, one ``<li>`` each, even for one."""
    return f"{GLOSSES_OPEN}{lang}>" + "".join(f"<li>{g}</li>" for g in glosses) + "</ol>"


def present_sense(row_html: str, *, styled: bool) -> str:
    """One stored row as a sense BODY (no ``<li class="gloss-item">`` wrapper).

    ``styled`` keeps the row's own markup (unwrapped only). Otherwise a row made of
    recognised lists becomes a ``gloss-glosses`` list of its glosses plus a
    ``gloss-note`` block of any notes; ``references``/``antonyms``/``sourceLanguages``
    drop. A row with no block content is one gloss, a one-item ``gloss-glosses`` list.
    A JMdict XML ``<ol>`` row becomes its senses, each a one-item list. Every other
    row passes through verbatim.
    """
    inner = _unwrap(row_html)
    if styled:
        return inner

    xml = _XML_SENSES_RE.fullmatch(inner)
    if xml and "<ol" not in inner[4:] and "<ul" not in inner:
        senses = [str(s) for s in _XML_SENSE_RE.findall(xml.group(1))]
        if len(senses) == 1:
            return _gloss_list(senses)
        return (
            '<ol class="gloss-list">'
            + "".join(f'<li class="gloss-item">{_gloss_list([s])}</li>' for s in senses)
            + "</ol>"
        )

    if inner and not any(tag in inner for tag in _BLOCK_TAGS):
        return _gloss_list([inner])

    lists = _recognised_lists(inner)
    if lists is None:
        return inner

    glosses: list[str] = []
    notes: list[str] = []
    lang = ""
    for key, attrs, items in lists:
        if key in _GLOSS_KEYS:
            glosses.extend(items)
            found = _LANG_RE.search(attrs)
            if not lang and found:
                lang = found.group(0)
        elif key == _NOTE_KEY:
            notes.extend(items)

    note_block = f'<div class="gloss-note">{"; ".join(notes)}</div>' if notes else ""
    if not glosses:
        return note_block
    return _gloss_list(glosses, lang) + note_block
