"""Glossary markup adapted for the curator's ``QTextBrowser`` definition pane.

The pane shows the SAME HTML a mined card carries (``IndexedDictProvider._render``
output), but Qt's rich-text engine is not a browser: ``QTextDocument`` implements a
small CSS subset, so the card's stylesheet is inert there. Measured against Qt 6.11:

============================================  =========
feature ``glossary.css`` is built on          supported
============================================  =========
``::before`` / ``::after`` generated content  no
``:not([data-has-styles])`` gates             no
``var(--am-chip)`` / ``color-mix()``          no
``padding`` / ``border`` on an inline span    no
class / type / ``[attr="value"]`` selectors   yes
``color`` / ``background-color`` / ``font-*`` yes
block ``margin``                              yes
``list-style-type`` / ``-qt-list-indent``     yes
============================================  =========

Every rule in ``glossary.css`` is gated on ``ol[data-count] … :not([data-has-styles])``
and most of its layout is generated content, so handing that sheet to the pane paints
nothing. Hence this module: a Qt-subset sheet (:data:`PREVIEW_CSS`) plus the smallest
markup adaptation that Qt's parser needs (:func:`to_preview_html`).

Two things CSS alone cannot fix here, both verified against real JMdict output:

* **Chips carry no separator.** ``_render`` joins ``<span class="gloss-tag">`` with
  nothing and lets ``margin-right: 0.4em`` do the spacing — an inline-box property Qt
  drops, so the row reads ``★123adj-iichi``. The separator has to enter the markup.
* **Qt discards an ``<li>`` whose content is purely a block element.** A sense is
  ``<li class="gloss-item"><div class="gloss-content"><ul …>``, so the sense level
  vanishes and every gloss of every sense lands in one flat list. Inlining each
  sense's glossary list into its own ``<li>`` as text restores the level (and the
  sense numbering) in one substitution.

Scope, deliberately:

* **The card pipeline is not touched.** ``indexed_provider``, ``glossary.css`` and
  ``card_style_block`` are what Anki renders; changing chip markup for the benefit of a
  preview pane would change every mined card.
* **Per-dictionary ``styles.css`` is ignored.** It is modern CSS written for a browser;
  Qt cannot apply it. Consequence: the preview is deliberately BLIND to
  ``data-has-styles``. On a card that stamp hands chrome to the dictionary's own sheet,
  but in the pane that sheet does not exist, so a stamp-aware preview would render
  Jitendex (which ships 6.4 KB of ``styles.css``) completely bare. Every entry gets
  the same treatment here.

Pure string transform: no Qt import, no I/O. The Qt-side contract — that these rules
resolve the way the docstring claims — is pinned by ``QTextDocument`` assertions in
``tests/unit/test_definition_preview_html.py``.
"""

from __future__ import annotations

import html
import re

# The sheet is applied with ``QTextDocument.setDefaultStyleSheet`` and must precede
# every ``setHtml``. Colors follow glossary.css's discipline — solid/rgba grays that
# read on a light and a dark palette alike — rather than importing the Theme singleton
# into a service module.
#
# ``list-style-type: none`` on the envelope ``<ol>`` resolves to Qt's
# ``ListStyleUndefined`` (no marker drawn); that is what suppresses the stray "1."
# the card envelope would otherwise leak into the pane.
PREVIEW_CSS = """
ol[data-count] { list-style-type: none; -qt-list-indent: 0; }
li[data-dictionary] { margin-bottom: 12px; }
li[data-dictionary] > i { color: rgba(150, 150, 150, 0.95); font-size: 8pt; }

ul.gloss-list { list-style-type: decimal; -qt-list-indent: 1; }
li.gloss-item { margin-bottom: 3px; }
ol.gloss-sc-ol { list-style-type: decimal; -qt-list-indent: 1; }
ul.gloss-sc-ul { list-style-type: none; -qt-list-indent: 1; }
li.gloss-sc-li { margin-bottom: 2px; }

/* One neutral chip for every dictionary-level tag, as the card sheet does with
 * --am-chip. Per-category tints were tried and dropped: the "popular" chip's label is
 * the ⭐ emoji, which renders in its own color and washed out against a tinted
 * background on a light palette. Structured-content tags below keep their tints —
 * their labels are plain words, so the contrast holds either way. */
span.gloss-tag { background-color: rgba(128, 128, 128, 0.22); font-size: 8pt; }

a.gloss-sc-a { color: #6f9dff; }

/* The data-sc-* hook set card_style_block._SC_GAPFILL_HOOKS enumerates, mapped to the
 * plain attribute selectors Qt does support. Kept in step with that tuple by a drift
 * test so a new hook cannot land unstyled in the pane. */
[data-sc-class="tag"] { background-color: rgba(128, 128, 128, 0.22); font-size: 8pt; }
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

# The two chip forms, both of which the renderer emits with no separator. The inner
# text is already HTML-escaped upstream and is re-emitted verbatim.
#
# * ``class="gloss-tag"`` — the dictionary-level chips ``indexed_provider._render``
#   builds from the tags table (``★``, ``adj-i``, sense ordinals).
# * ``data-sc-class="tag"`` — structured-content tags a dictionary authors inside its
#   own senses (Jitendex writes ``adjective``/``kana`` this way). Same run-together
#   failure, same fix; matched on the attribute because the class attribute is the
#   generic ``gloss-sc-span``.
_CHIP_RE = re.compile(
    r'(<span (?:class="gloss-tag"|[^>]*?\bdata-sc-class="tag")[^>]*>)(.*?)(</span>)',
    re.DOTALL,
)

# Chips of either form sitting flush against each other.
_CHIP_SEAM_RE = re.compile(r'(</span>)(<span (?:class="gloss-tag"|[^>]*?\bdata-sc-class="tag"))')

# A reference label ("See also") butts straight against the term it introduces.
_REFERENCE_LABEL_RE = re.compile(r'(<span[^>]*\bdata-sc-content="reference-label"[^>]*>)(.*?)(</span>)', re.DOTALL)

# Chip padding. Qt ignores ``padding`` on an inline span, so the pill's breathing room
# has to be text; NBSP survives the parser where a plain space at a run edge would be
# collapsed away. The separator BETWEEN chips is a normal space so the row can wrap.
_NBSP = " "

# The renderer's attribution line where it butts against the chip row with no
# separator ("…uk(JMdict [2026-06-28])"). Anchored on the preceding ``</span>`` rather
# than on ``<i>(`` alone, which would also break a parenthesised italic inside a gloss.
# Applied to EVERY match, not just the first: an entry with several sequence groups
# emits one chips-plus-attribution block per group. A group with no chips needs no
# rule — its attribution follows a ``</ul>``, and Qt starts a new paragraph there.
_ATTRIBUTION_RE = re.compile(r"(</span>)(<i>\()")

# A sense's glossary list. Scoped to ``data-sc-content="glossary"`` on purpose: the
# same ``gloss-sc-ul`` class also carries ``references``/``sense-groups``, and an
# unscoped match swallows the see-also block into the sense line
# ("immenselysee: 凄く …"). Non-greedy up to the first ``</ul>`` is safe because a
# glossary list never nests another list.
_GLOSSARY_UL_RE = re.compile(
    r'<ul class="gloss-sc-ul"(?=[^>]*\sdata-sc-content="glossary")[^>]*>(.*?)</ul>',
    re.DOTALL,
)
_GLOSS_ITEM_RE = re.compile(r'<li class="gloss-sc-li"[^>]*>(.*?)</li>', re.DOTALL)

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
    nothing but block elements, so a sense whose only child is a ``<ul>`` disappears
    and its glosses merge into the surrounding list.
    """

    def replace(match: re.Match[str]) -> str:
        items = [item.strip() for item in _GLOSS_ITEM_RE.findall(match.group(1))]
        items = [item for item in items if item]
        if not items:
            return match.group(0)
        return f'<span class="gloss-sc-span" data-sc-content="glossary">{", ".join(items)}</span>'

    return _GLOSSARY_UL_RE.sub(replace, entry_html)


def adapt_entry(entry_html: str) -> str:
    """One provider entry, adapted for Qt's rich-text engine.

    Idempotent: an already-adapted entry is returned unchanged, so re-rendering a
    cached lookup cannot stack padding on the chips.
    """
    if not entry_html or _ADAPTED in entry_html:
        return entry_html
    adapted = _inline_glossaries(entry_html)
    adapted = _pad_chips(adapted)
    adapted = _ATTRIBUTION_RE.sub(r"\1<br>\2", adapted)
    return adapted.replace('<div class="yomitan-glossary">', f'<div class="yomitan-glossary"{_ADAPTED}>', 1)


#: The trailing attribution line ``IndexedDictProvider._render`` emits per sense
#: group. Its content is ``", ".join(fallback_tags + [dict_label])``, escaped, so
#: the dictionary name is one comma-separated token — not necessarily the whole
#: parenthesis. ``[^<]*`` cannot cross a tag, and being greedy it backtracks to the
#: LAST ``)``, so a name that itself ends in a parenthesis survives intact.
_ENTRY_ATTRIBUTION_RE = re.compile(r"<i>\(([^<]*)\)</i>")


def _entry_names_itself(adapted: str, name: str) -> bool:
    """Whether ``adapted``'s own attribution line already prints ``name``.

    Membership of the comma-separated token list, not a substring test for
    ``<i>(Name)</i>``: any tag missing from the dictionary's tag bank is rendered
    as a fallback tag INSIDE that same parenthesis (``(uk, adj-i, JMdict)``), and
    a dictionary shipping no ``tag_bank_*.json`` — or one whose tags read fails —
    turns every tag into a fallback tag, so the substring test missed for the
    whole dictionary and the pane printed a bold heading above an entry already
    ending in ``, JMdict)``.
    """
    needle = html.escape(name, quote=True)
    return any(
        needle in [token.strip() for token in match.group(1).split(",")]
        for match in _ENTRY_ATTRIBUTION_RE.finditer(adapted)
    )


def to_preview_html(entries: list[tuple[str, str]]) -> str:
    """The pane body for ``DefinitionService.lookup_all_offline`` output.

    Each entry already names its own dictionary in the trailing ``<i>(…)</i>`` line,
    so the provider name is emitted as a heading only when the entry does not carry
    it — otherwise the pane showed the dictionary twice.
    """
    parts: list[str] = []
    for name, entry_html in entries:
        adapted = adapt_entry(entry_html)
        if not _entry_names_itself(adapted, name):
            parts.append(f'<p style="font-weight:bold">{html.escape(name)}</p>')
        parts.append(adapted)
    return "".join(parts)
