"""Glossary markup adapted for the curator's ``QTextBrowser`` definition pane.

The pane shows the SAME HTML a mined card carries (``IndexedDictProvider._render``
output), but Qt's rich-text engine is not a browser: ``QTextDocument`` implements a
small CSS subset, so the card's stylesheet is inert there. Measured against Qt 6.11:

============================================  =========
CSS feature                                   supported
============================================  =========
``::before`` / ``::after`` generated content  no
``:not(…)`` selectors                         no
``var(…)`` / ``color-mix()``                  no
``padding`` / ``border`` on an inline span    no
class / type / ``[attr="value"]`` selectors   yes
``color`` / ``background-color`` / ``font-*`` yes
block ``margin``                              yes
``list-style-type`` / ``-qt-list-indent``     yes
============================================  =========

Hence this module: a Qt-subset sheet (:data:`PREVIEW_CSS`) instead of the card's,
plus the smallest markup adaptation that Qt's parser needs (:func:`to_preview_html`).

Two things CSS alone cannot fix here:

* **Tags carry no separator.** Jitendex and Wiktionary write their sense tags as
  flush ``<span>`` runs and let ``margin`` do the spacing — an inline-box property Qt
  drops, so the row reads ``adjectivekana``. The separator has to enter the markup.
* **Qt discards an ``<li>`` whose content is purely a block element.** A sense is
  ``<li class="gloss-item">`` holding only its glossary list (``ol.gloss-glosses``,
  even for one gloss, or a styled dictionary's ``ul.gloss-sc-ul``), so the sense
  level vanishes and every gloss of every sense lands in a flat list. Inlining each
  sense's glossary list into its own ``<li>`` as text restores the level (and the
  sense numbering) in one substitution.

Scope, deliberately:

* **The card pipeline is not touched.** ``indexed_provider``, ``glossary.css`` and
  ``card_style_block`` are what Anki renders; changing markup for the benefit of a
  preview pane would change every mined card.
* **Per-dictionary ``styles.css`` is ignored.** It is modern CSS written for a browser;
  Qt cannot apply it. A dictionary that ships one (Jitendex: 6.4 KB) gets the same
  Qt-subset treatment as every other entry, through the ``data-sc-*`` rules below.

Pure string transform: no Qt import, no I/O. The Qt-side contract — that these rules
resolve the way the docstring claims — is pinned by ``QTextDocument`` assertions in
``tests/unit/test_definition_preview_html.py``.
"""

from __future__ import annotations

import html
import re

from anki_miner.services.dictionary.glossary_label import display_title
from anki_miner.services.dictionary.sense_presentation import SYNTHESIZED_LIST_ATTRS

# The sheet is applied with ``QTextDocument.setDefaultStyleSheet`` and must precede
# every ``setHtml``. Colors follow glossary.css's discipline — solid/rgba grays that
# read on a light and a dark palette alike — rather than importing the Theme singleton
# into a service module.
#
# ``list-style-type: none`` on the envelope ``<ol>`` resolves to Qt's
# ``ListStyleUndefined`` (no marker drawn); that is what suppresses the stray "1."
# the card envelope would otherwise leak into the pane.
#
# The envelope ``<li>``'s bottom margin only spaces a one-sense entry, whose body is
# inline. A multi-sense envelope holds nothing but its ``gloss-list``, so Qt drops that
# ``<li>`` and its margin with it; there the bold heading and the restarted sense
# numbering are what set the entries apart.
PREVIEW_CSS = """
ol[data-count] { list-style-type: none; -qt-list-indent: 0; }
li[data-dictionary] { margin-bottom: 12px; }

ol.gloss-list { list-style-type: decimal; -qt-list-indent: 1; }
li.gloss-item { margin-bottom: 3px; }
.gloss-note { color: rgba(150, 150, 150, 0.95); }
ol.gloss-sc-ol { list-style-type: decimal; -qt-list-indent: 1; }
ul.gloss-sc-ul { list-style-type: none; -qt-list-indent: 1; }
li.gloss-sc-li { margin-bottom: 2px; }

a.gloss-sc-a { color: #6f9dff; }

/* The data-sc-* hooks a dictionary that keeps its own markup (Jitendex, wty) writes
 * into its senses, mapped to the plain attribute selectors Qt does support. Its own
 * styles.css cannot reach the pane, so these are the only chrome those senses get. */
[data-sc-class="tag"] { background-color: rgba(128, 128, 128, 0.22); font-size: 8pt; }
[data-sc-content="tag"] { background-color: rgba(128, 128, 128, 0.22); font-size: 8pt; }
[data-sc-content="part-of-speech-info"] { background-color: rgba(120, 190, 120, 0.26); }
[data-sc-content="misc-info"] { background-color: rgba(200, 120, 90, 0.26); }
[data-sc-content="field-info"] { background-color: rgba(160, 120, 200, 0.26); }
[data-sc-content="dialect-info"] { background-color: rgba(200, 170, 90, 0.26); }
[data-sc-content="antonym"] { color: rgba(150, 150, 150, 0.95); }
[data-sc-content="xref"] { color: rgba(150, 150, 150, 0.95); }
[data-sc-content="reference-label"] { color: rgba(150, 150, 150, 0.95); }
[data-sc-content="sense-note"] { color: rgba(150, 150, 150, 0.95); font-style: italic; }
[data-sc-content="info-gloss"] { color: rgba(150, 150, 150, 0.95); font-style: italic; }
[data-sc-content="lang-source"] { color: rgba(150, 150, 150, 0.95); }
[data-sc-content="lang-source-wasei"] { color: rgba(150, 150, 150, 0.95); }
[data-sc-content="forms"] { color: rgba(150, 150, 150, 0.95); }
[data-sc-content="example-sentence"] { color: rgba(150, 150, 150, 0.95); }
[data-sc-content="example-sentence-a"] { color: rgba(150, 150, 150, 0.95); }
[data-sc-content="example-sentence-b"] { color: rgba(150, 150, 150, 0.95); }
[data-sc-content="extra-info"] { color: rgba(150, 150, 150, 0.95); }
[data-sc-content="attribution"] { color: rgba(150, 150, 150, 0.95); font-size: 8pt; }
[data-sc-content="frequency"] { color: rgba(150, 150, 150, 0.95); font-size: 8pt; }
[data-sc-content="pitch-accent"] { color: rgba(150, 150, 150, 0.95); }
[data-sc-class="extra-box"] { color: rgba(150, 150, 150, 0.95); }
[data-sc-class="extra-label"] { color: rgba(150, 150, 150, 0.95); font-size: 8pt; }
"""

# Structured-content tags a dictionary authors inside its own senses, which arrive
# flush against each other with no separator. Jitendex writes ``adjective``/``kana``
# as ``data-sc-class="tag"``; matched on the attribute because the class attribute
# is the generic ``gloss-sc-span``. Wiktionary (wty) dictionaries write the same tag
# as ``data-sc-content="tag"``, inside a ``data-sc-content="tags"`` div that the
# closing quote keeps out. The inner text is already HTML-escaped upstream and is
# re-emitted verbatim.
_CHIP_RE = re.compile(r'(<span [^>]*?\bdata-sc-(?:class|content)="tag"[^>]*>)(.*?)(</span>)', re.DOTALL)

# Tags sitting flush against each other.
_CHIP_SEAM_RE = re.compile(r'(</span>)(<span [^>]*?\bdata-sc-(?:class|content)="tag")')

# A reference label ("See also") butts straight against the term it introduces.
_REFERENCE_LABEL_RE = re.compile(r'(<span[^>]*\bdata-sc-content="reference-label"[^>]*>)(.*?)(</span>)', re.DOTALL)

# Chip padding. Qt ignores ``padding`` on an inline span, so the pill's breathing room
# has to be text; NBSP survives the parser where a plain space at a run edge would be
# collapsed away. The separator BETWEEN chips is a normal space so the row can wrap.
_NBSP = " "

# A sense's glossary list, in any of the three forms a sense body carries:
#
# * ``<ol class="gloss-glosses">`` with bare ``<li>`` items — what ``present_sense``
#   makes of an unstyled dictionary's glosses, a sense's only one included.
# * ``<ul class="gloss-sc-ul" … data-sc-content="glossary">`` with ``li.gloss-sc-li``
#   items — the stored row markup a dictionary with its own styles.css keeps.
#   Scoped to ``data-sc-content="glossary"`` on purpose: the same ``gloss-sc-ul``
#   class also carries ``references``/``sense-groups``, and an unscoped match
#   swallows the see-also block into the sense line ("immenselysee: 凄く …").
# * ``<ul class="gloss-sc-ul" style="list-style-type: circle">`` with ``li.gloss-sc-li``
#   items — the members list the renderer synthesizes for a row with several glossary
#   members, which a styled dictionary's row keeps too (wty-pt-en ``dá``).
#
# Each body runs to the first closing tag and may not open another list, so a list
# that nests one is left as it is instead of being cut at the inner list's end (the
# nested glossary list still collapses on its own). ``present_sense``'s block note
# (``div.gloss-note``) right after the list is captured with it: once the list is
# text, that block would leave its sense and render as an unindented line.
_FLAT_LIST_BODY = r"((?:(?!<[ou]l[\s>]).)*?)"
_GLOSS_LIST_RE = re.compile(
    r'(?:<ul class="gloss-sc-ul"(?=[^>]*\sdata-sc-content="glossary")[^>]*>' + _FLAT_LIST_BODY + "</ul>"
    r'|<ul class="gloss-sc-ul"' + re.escape(SYNTHESIZED_LIST_ATTRS) + ">" + _FLAT_LIST_BODY + "</ul>"
    r'|<ol class="gloss-glosses"[^>]*>' + _FLAT_LIST_BODY + "</ol>)"
    r'(?:<div class="gloss-note">(.*?)</div>)?',
    re.DOTALL,
)
_GLOSS_ITEM_RE = re.compile(r'<li(?: class="gloss-sc-li")?[^>]*>(.*?)</li>', re.DOTALL)

# Marker for "this document has already been adapted", so a re-render of cached
# entries cannot double-pad the chips.
_ADAPTED = ' data-am-preview=""'


def _pad_chips(entry_html: str) -> str:
    """Give every chip inner padding and a gap from its neighbour."""
    padded = _CHIP_RE.sub(lambda m: f"{m.group(1)}{_NBSP}{m.group(2)}{_NBSP}{m.group(3)}", entry_html)
    padded = _CHIP_SEAM_RE.sub(r"\1 \2", padded)
    return _REFERENCE_LABEL_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{_NBSP}{m.group(3)}", padded)


def _inline_glossaries(entry_html: str) -> str:
    """Collapse each sense's glossary list into comma-joined text in its own ``<li>``.

    This is the step that keeps the sense level alive: Qt drops an ``<li>`` holding
    nothing but block elements, so a sense whose only child is a glossary list
    (``ol.gloss-glosses`` or ``ul.gloss-sc-ul``) disappears and its glosses merge
    into the surrounding list. A block note under the list becomes a line break
    plus an inline note, so it stays inside the sense.
    """

    def replace(match: re.Match[str]) -> str:
        body = match.group(1) or match.group(2) or match.group(3) or ""
        items = [item.strip() for item in _GLOSS_ITEM_RE.findall(body)]
        items = [item for item in items if item]
        if not items:
            return match.group(0)
        note = match.group(4)
        note_html = f'<br><span class="gloss-note">{note}</span>' if note is not None else ""
        return f'<span class="gloss-sc-span" data-sc-content="glossary">{", ".join(items)}</span>{note_html}'

    return _GLOSS_LIST_RE.sub(replace, entry_html)


def adapt_entry(entry_html: str) -> str:
    """One provider entry, adapted for Qt's rich-text engine.

    Idempotent: an already-adapted entry is returned unchanged, so re-rendering a
    cached lookup cannot stack padding on the chips.
    """
    if not entry_html or _ADAPTED in entry_html:
        return entry_html
    adapted = _inline_glossaries(entry_html)
    adapted = _pad_chips(adapted)
    return adapted.replace('<div class="yomitan-glossary">', f'<div class="yomitan-glossary"{_ADAPTED}>', 1)


def to_preview_html(entries: list[tuple[str, str]]) -> str:
    """The pane body for ``DefinitionService.lookup_all_offline`` output.

    Each run of entries from one provider is headed once by its short name
    (``display_title``, as a card field stacking two or more dictionaries labels
    it): the renderer prints no dictionary label of its own, and a provider's exact
    hit and its ladder hits arrive one after another.
    """
    parts: list[str] = []
    previous: str | None = None
    for name, entry_html in entries:
        if name != previous:
            parts.append(f'<p style="font-weight:bold">{html.escape(display_title(name))}</p>')
            previous = name
        parts.append(adapt_entry(entry_html))
    return "".join(parts)
