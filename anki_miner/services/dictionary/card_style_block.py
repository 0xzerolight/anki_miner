"""Self-contained per-field glossary ``<style>`` block (Yomitan model).

Anki Miner emits glossary CSS *inside each styled field* as one TRAILING
``<style>`` block, so styling travels with the note — it works on any note type,
on AnkiDroid/mobile, in exports, and when a card is shared, and nothing can strip
or de-sync it. This is how Yomitan delivers glossary CSS (self-contained field
HTML), and it replaces the shared note-type CSS block that the v2.7.6 rework
introduced (Anki Miner no longer writes note-type styling at all).

Two placement invariants, both load-bearing for JS-driven note types (Kiku
class) that hold fields in inert ``<template>`` elements and re-inject them
page-by-page through ``DOMParser`` → ``doc.body.innerHTML``:

* **Per field, not per card.** A ``<style>`` in one field is only card-wide on
  note types that render all fields into one live document. Field-isolating
  note types show each field alone, so EVERY styled field must carry its own
  block (Yomitan does the same and duplicates dict CSS across fields).
* **Trailing, never leading.** The HTML parser hoists a *leading* ``<style>``
  into ``<head>``, so any ``body.innerHTML`` round-trip silently drops it; a
  trailing block stays in ``<body>`` and survives (jsdom-verified against
  Kiku's actual pipeline). ``attach_card_style_block`` is the only sanctioned
  attach seam and enforces both invariants.

The block is ``[base glossary.css] + [scoped per-dictionary CSS]`` (base →
dict-author, following the Yomitan ``_getCustomCss`` ordering). The base is
minified on embed (comments + whitespace stripped): the seven structural core
rules always, plus the ``images`` / ``tables`` regions (delimited in
``glossary.css`` by ``@am-group`` marker comments) only when the field itself
contains markup they style (``base_css``). Only the base — which we author — is
minified; a dictionary's own CSS is embedded verbatim.

Pure string transform: no Qt, no HTTP, no disk I/O beyond ``load_glossary_css``'s
bundled read. The scoped per-dictionary CSS is gathered separately by
``definition_service.collect_dictionary_css`` and passed in as ``dict_css``.
"""

from __future__ import annotations

import html as html_lib
import re
from collections.abc import Iterable
from functools import lru_cache

from anki_miner.services.dictionary.card_style_presets import load_glossary_css

# Conservative CSS minifier: strip /* */ comments, collapse all whitespace runs
# to a single space, and tighten spacing around block/statement delimiters. It
# deliberately does NOT touch spacing around ``:`` or ``>`` so selector combinators
# and property values (e.g. ``mask-image: var(--image)``, ``a > b``) are never
# altered. Safe for our authored glossary.css; halves its embedded size.
#
# String-literal aware: whitespace collapsing and delimiter-tightening run only
# OUTSIDE quoted strings, and ``/* */`` inside a string is literal text, not a
# comment. So a rule like ``content: "a, b"`` keeps its comma/space verbatim
# instead of being corrupted to ``content:"a,b"``. Output is byte-identical to a
# naive global pass whenever no comma/semicolon lives inside a quoted string —
# which is true of today's glossary.css (its only commas separate selector lists,
# ``rgba()`` arguments and a ``var()`` fallback, where tightening is valid CSS).
_COMMENT_OR_STRING_RE = re.compile(
    r"""/\*.*?\*/          # a CSS comment
        | "(?:\\.|[^"\\])*"  # a double-quoted string (with escapes)
        | '(?:\\.|[^'\\])*'  # a single-quoted string (with escapes)
    """,
    re.DOTALL | re.VERBOSE,
)
_STRING_RE = re.compile(r"""\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'""", re.DOTALL)
_WS_RE = re.compile(r"\s+")
_DELIM_RE = re.compile(r"\s*([{};,])\s*")


def _minify_css(css: str) -> str:
    # Pass 1: drop comments, but never one that is actually inside a string
    # literal (a ``/*`` in ``content:"…"`` is text, not a comment start).
    css = _COMMENT_OR_STRING_RE.sub(lambda m: "" if m.group().startswith("/*") else m.group(), css)
    # Pass 2: collapse whitespace + tighten ``{ } ; ,`` only between string
    # literals; emit each string verbatim so its inner commas/spaces survive.
    out: list[str] = []
    pos = 0
    for m in _STRING_RE.finditer(css):
        chunk = _DELIM_RE.sub(r"\1", _WS_RE.sub(" ", css[pos : m.start()]))
        out.append(chunk)
        out.append(m.group())
        pos = m.end()
    out.append(_DELIM_RE.sub(r"\1", _WS_RE.sub(" ", css[pos:])))
    return "".join(out).strip()


# ---------------------------------------------------------------------------
# Optional regions. glossary.css marks two of them with
# `/* @am-group: <name> */ … /* @am-endgroup */`; unmarked text is the
# always-embedded core. A region joins a field's block iff the field contains
# one of its literal probes. The probes over-include (a stray substring costs
# a few hundred bytes, a miss would leave an image or table unstyled).
# ---------------------------------------------------------------------------

OWNED_STYLE_MARKER = ".yomitan-glossary{--anki-miner-owned-style: 1;"

_REGION_PROBES: dict[str, tuple[str, ...]] = {
    "images": ("gloss-image",),
    # `<summary` too: the renderer allows a bare <summary> node outside <details>.
    "tables": ("<table", "<details", "<summary"),
}

_GROUP_MARKER_RE = re.compile(r"/\*\s*@am-group:\s*([a-z-]+)\s*\*/|/\*\s*@am-endgroup\s*\*/")


def split_group_regions(css: str) -> list[tuple[str, str]]:
    """Partition raw ``glossary.css`` text into ordered ``(group, css)`` regions.

    Text outside any marker pair is ``core``. Markers must not nest; an
    ``@am-group`` inside an open region or a dangling ``@am-endgroup`` raises —
    authoring errors should fail loudly at import/test time, never ship a
    half-split sheet.
    """
    regions: list[tuple[str, str]] = []
    pos = 0
    current = "core"
    for match in _GROUP_MARKER_RE.finditer(css):
        opened = match.group(1)
        if opened is not None and current != "core":
            raise ValueError(f"nested @am-group {opened!r} inside {current!r}")
        if opened is None and current == "core":
            raise ValueError("@am-endgroup without an open @am-group")
        regions.append((current, css[pos : match.start()]))
        current = opened if opened is not None else "core"
        pos = match.end()
    if current != "core":
        raise ValueError(f"unclosed @am-group {current!r}")
    regions.append((current, css[pos:]))
    return regions


@lru_cache(maxsize=1)
def _minified_regions() -> tuple[tuple[str, str], ...]:
    """Ordered ``(group, minified_css)`` regions, empty regions dropped.

    Fails loudly on a group ``base_css`` has no probe for (it would never
    embed) and on a core that breaks the restyler's contract: it must open
    with ``OWNED_STYLE_MARKER``, carry the ``ol[data-count]`` token, and no
    region may hold a newline (the restyler splits the block at the first one).
    """
    regions = []
    for group, raw in split_group_regions(load_glossary_css()):
        if group != "core" and group not in _REGION_PROBES:
            raise ValueError(f"unknown style group {group!r}")
        minified = _minify_css(raw)
        if minified:
            regions.append((group, minified))
    core = "".join(css for group, css in regions if group == "core")
    if not core.startswith(OWNED_STYLE_MARKER) or "ol[data-count]" not in core:
        raise ValueError("glossary.css core lost the OWNED_STYLE_MARKER/ol[data-count] contract")
    if any("\n" in css for _, css in regions):
        raise ValueError("minified glossary.css carries a newline")
    return tuple(regions)


def base_css(field_html: str) -> str:
    """The minified base sheet for one field: the core, plus each optional
    region whose literal probe occurs in ``field_html``, in document order (so
    cascade order among the embedded rules is unchanged).

    Joined with ``""``: markers sit right after a ``}``, so with every region
    present the result equals ``_minify_css(load_glossary_css())`` byte-for-byte.
    """
    return "".join(
        css
        for group, css in _minified_regions()
        if group == "core" or any(probe in field_html for probe in _REGION_PROBES[group])
    )


#: Appended AFTER the base sheet and the scoped dictionary CSS, inside the same
#: per-field block, for a right-to-left mining language only (S21; the profile's
#: ``content_style.direction``). The glossary stays left-to-right -- wty's
#: ar/fa/he glosses are English -- but the native example sentence each gloss
#: carries (``data-sc-content="example-sentence-a"``: ~5,000 per wty ar/fa/he
#: dictionary, a quarter or more ending in ASCII punctuation) would show that
#: punctuation on the wrong end of an LTR paragraph. ``unicode-bidi: plaintext``
#: gives each such block the direction of its own first strong character: an
#: Arabic or Hebrew example reads right-to-left, a Latin one stays left-to-right.
#: Guarded by ``ol[data-count]`` like every glossary.css rule, one line, and free
#: of ``[data-dictionary=...]`` selectors, so the restyler carries it verbatim as
#: part of the block tail and never reads it as a dictionary's own CSS.
#: glossary.css itself is never edited (it is embedded in every card).
RTL_GLOSSARY_CSS = '.yomitan-glossary ol[data-count] [data-sc-content="example-sentence-a"]{unicode-bidi:plaintext}'


def build_card_style_block(*, dict_css: str, card_html: str, direction: str = "ltr") -> str:
    """Assemble the self-contained ``<style>`` block for ONE field.

    ``[base_css(card_html)] + [dict_css]``, wrapped in a single ``<style>``
    element. ``card_html`` is the ONE field's own miner-markup HTML and picks
    the optional base regions; it is a REQUIRED keyword so a caller can never
    silently fall back to a core-only block. Every writer (mining, backfill,
    restyle) picks per field, or their outputs diverge and the restyler
    rewrites fresh cards forever — and field-isolating note types (module
    docstring) never see the other field anyway. ``dict_css`` is the
    already-scoped per-dictionary CSS (filtered to this field via
    ``filter_dict_css_entries``), embedded verbatim. ``direction == "rtl"``
    appends :data:`RTL_GLOSSARY_CSS` as the last section. Returns ``""`` only if
    every section is empty (the core is never empty, so in practice this always
    returns a block).
    """
    sections = [base_css(card_html)]
    scoped = dict_css.strip()
    if scoped:
        sections.append(scoped)
    if direction == "rtl":
        sections.append(RTL_GLOSSARY_CSS)
    body = "\n".join(section for section in sections if section)
    if not body.strip():
        return ""
    return f"<style>{body}</style>"


# Dictionary envelopes in a field body, whatever attributes follow the title
# and stable ID. Restricting the probe to ``<li>`` avoids treating selectors in
# carried legacy ``<style>`` blocks as card envelopes.
_ENVELOPE_RE = re.compile(r'<li data-dictionary="([^"]*)"(?: data-dictionary-id="([^"]*)")?')

# The miner-markup fingerprint a field must carry before any styling attaches.
# Same probe the restyler uses to recognize miner fields; a field without it
# has nothing our CSS could style, and attaching a block to empty content
# would emit a field-LEADING <style> — the head-hoist hazard (module docstring).
_MINER_MARKUP_TOKENS = ("yomitan-glossary", "data-count")


def filter_dict_css_entries(field_html: str, entries: Iterable[tuple[str, str, str]]) -> str:
    """Join the scoped CSS of exactly the dictionaries present in ``field_html``.

    ``entries`` is ``collect_dictionary_css_entries`` output: ordered
    ``(dict_id, display_name, scoped_css)`` triples, duplicates preserved. New
    envelopes carrying ``data-dictionary-id`` match only by stable ID, preserving
    same-title isolation. Legacy envelopes lacking that attribute match by their
    ``html.unescape``d display title for back-compat.
    """
    envelopes = [
        (html_lib.unescape(title), html_lib.unescape(dict_id) if dict_id else None)
        for title, dict_id in _ENVELOPE_RE.findall(field_html)
    ]
    ids = {dict_id for _, dict_id in envelopes if dict_id is not None}
    legacy_titles = {title for title, dict_id in envelopes if dict_id is None}
    return "\n\n".join(css for dict_id, display_name, css in entries if dict_id in ids or display_name in legacy_titles)


def attach_card_style_block(
    field_html: str, *, dict_css_entries: Iterable[tuple[str, str, str]], direction: str = "ltr"
) -> str:
    """Return ``field_html`` with its self-contained TRAILING ``<style>`` block.

    The one sanctioned attach seam for fresh writers (mining, backfill); it
    enforces both module-docstring invariants:

    * A field without miner markup (or empty) is returned UNCHANGED — never a
      leading block, never styling on content we don't own.
    * Otherwise the block is appended AFTER the content — trailing survives the
      ``DOMParser`` → ``body.innerHTML`` round-trips of JS note types; leading
      does not.

    The embedded dict CSS is filtered to the dictionaries present in this field
    (``filter_dict_css_entries``) and the base regions are picked against this
    field only. ``direction`` is the active profile's
    ``content_style.direction`` (see :data:`RTL_GLOSSARY_CSS`).
    """
    if not field_html or any(token not in field_html for token in _MINER_MARKUP_TOKENS):
        return field_html
    block = build_card_style_block(
        dict_css=filter_dict_css_entries(field_html, dict_css_entries),
        card_html=field_html,
        direction=direction,
    )
    return field_html + block
