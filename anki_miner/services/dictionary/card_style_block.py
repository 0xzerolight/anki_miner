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

The block is ``[tree-shaken base glossary.css] + [scoped per-dictionary CSS]``
(base → dict-author, following the Yomitan ``_getCustomCss`` ordering). The base
sheet is minified on embed (comments + whitespace stripped) and **tree-shaken
per card** (Issue #93): ``glossary.css`` is partitioned by ``@am-group`` marker
comments into an always-embedded core plus witness-gated groups
(unstyled-chrome / sc-gapfill / images / tables), and a group is embedded only
when the card's own HTML carries markup its rules could match
(``css_witnesses``). Dropping rules that cannot match anything on the card is
semantics-preserving by construction, so the render is identical on every Anki
client; detection errs toward over-inclusion (wasted bytes, never a missing
style). Only the base — which we author — is minified; a dictionary's own CSS is
embedded verbatim.

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
# which is true of today's glossary.css (its only commas are ``rgba()``/
# ``color-mix()`` separators, where tightening is valid CSS).
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
# Per-card tree-shaking (Issue #93). glossary.css is partitioned by
# `/* @am-group: <name> */ … /* @am-endgroup */` marker comments; unmarked text
# is the always-embedded core. A group is embedded iff the card's own HTML
# contains a WITNESS that any of its rules could match, so dropped rules are
# provably inert on that card. Placement is linted both ways in
# tests/unit/test_glossary_css.py; the region concat is pinned byte-identical
# to the whole-sheet minify by the ALL_GROUPS tripwire.
# ---------------------------------------------------------------------------

ALL_GROUPS = frozenset({"unstyled-chrome", "sc-gapfill", "images", "tables"})

_GROUP_MARKER_RE = re.compile(r"/\*\s*@am-group:\s*([a-z-]+)\s*\*/|/\*\s*@am-endgroup\s*\*/")

# A card body's UNSTAMPED dictionary envelope, exactly as the renderer emits it:
# `<li data-dictionary="TITLE">` with no other attribute. A stamped envelope
# carries ` data-has-styles=""` before the `>`, so it never matches — making
# this a per-envelope test that stays true on mixed styled+unstyled multi-dict
# cards (a global "no data-has-styles in card" check would under-include there).
# card_restyler imports this for legacy-envelope stamping; it lives here so the
# witness and the stamper cannot drift apart (and card_restyler already imports
# this module, so the dependency points this way).
UNSTAMPED_ENVELOPE_RE = re.compile(r'<li data-dictionary="([^"]*)">')


def split_group_regions(css: str) -> list[tuple[str, str]]:
    """Partition raw ``glossary.css`` text into ordered ``(group, css)`` regions.

    Text outside any marker pair is ``core``. Markers must not nest; an
    ``@am-group`` inside an open region or a dangling ``@am-endgroup`` raises —
    authoring errors should fail loudly at import/test time, never ship a
    half-shaken sheet.
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
    """Ordered ``(group, minified_css)`` regions, empty regions dropped."""
    regions = []
    for group, raw in split_group_regions(load_glossary_css()):
        minified = _minify_css(raw)
        if minified:
            regions.append((group, minified))
    return tuple(regions)


@lru_cache(maxsize=32)
def base_css_variant(groups: frozenset[str]) -> str:
    """The minified base sheet carrying core + the given groups, in document
    order (so cascade order among surviving rules is unchanged).

    Joined with ``""``: the whole-sheet minifier tightens whitespace around
    ``}``, so region boundaries — always immediately after a ``}`` — carry no
    separator, and ``base_css_variant(ALL_GROUPS) == _minify_css(load_glossary_css())``
    byte-for-byte. Every variant is newline-free and carries the
    ``ol[data-count]`` head-detection token (both load-bearing for
    ``card_restyler``), asserted here so a bad marker edit fails loudly.
    """
    unknown = groups - ALL_GROUPS
    if unknown:
        raise ValueError(f"unknown style groups: {sorted(unknown)}")
    css = "".join(m for g, m in _minified_regions() if g == "core" or g in groups)
    if "\n" in css or "ol[data-count]" not in css:
        raise ValueError("base variant broke the newline-free/ol[data-count] contract")
    return css


# The ``data-sc-*`` hooks the ``sc-gapfill`` rules actually target, as literal
# HTML-attribute fragments to substring-test against a card body. The sc-gapfill
# witness keys on THESE, not on a bare ``"data-sc-"`` check: every card carries
# ``data-sc-content="glossary"``, but that is styled by ``core`` (``.gloss-sc-ul``),
# so a coarse check kept the ~4.6 KB group on every card for nothing — defeating
# the Issue #93 tree-shake on the common (unstyled JMdict) case.
#
# The match form is load-bearing; do NOT "simplify" it to a bare value substring:
#   * exact ``=`` selector (``[data-sc-content="forms"]``) → the literal WITH its
#     closing quote, so ``formsTable``/``antonyms``/``gloss-tag`` cannot collide
#     with ``forms``/``antonym``/``tag`` and fire on every card;
#   * ``|=`` selector (``[data-sc-content|="frequency"]`` — matches the value OR a
#     ``value-`` hyphen prefix) → the OPEN-prefix literal WITHOUT the closing
#     quote, so ``frequency-nf01`` still fires. A closing-quoted literal there
#     would MISS the suffixed form and strip a needed style — the one forbidden
#     outcome; the open prefix is a safe superset. The renderer always emits
#     ``data-sc-<key>="value"`` double-quoted (yomitan_renderer ``_render_attrs``),
#     so these literals match its output exactly.
# Kept honest against glossary.css by the drift test in tests/unit/test_glossary_css.py
# (bidirectional equality + a broad-vs-narrow occurrence-count check), so a future
# rule with an exotic hook fails CI loudly instead of silently dropping a style.
_SC_GAPFILL_HOOKS = frozenset(
    {
        # ``|=`` selectors → open-prefix literal (matches ``value`` and ``value-*``).
        'data-sc-content="attribution',
        'data-sc-content="extra-info',
        'data-sc-content="frequency',
        'data-sc-content="pitch-accent',
        # exact ``=`` selectors → full closing-quoted literal.
        'data-sc-content="antonym"',
        'data-sc-content="dialect-info"',
        'data-sc-content="example-sentence"',
        'data-sc-content="example-sentence-a"',
        'data-sc-content="example-sentence-b"',
        'data-sc-content="field-info"',
        'data-sc-content="forms"',
        'data-sc-content="info-gloss"',
        'data-sc-content="lang-source"',
        'data-sc-content="lang-source-wasei"',
        'data-sc-content="misc-info"',
        'data-sc-content="part-of-speech-info"',
        'data-sc-content="reference-label"',
        'data-sc-content="sense-note"',
        'data-sc-content="xref"',
        'data-sc-class="extra-box"',
        'data-sc-class="extra-label"',
        'data-sc-class="tag"',
    }
)


def _has_sc_gapfill_hook(html: str) -> bool:
    """Whether ``html`` carries a ``data-sc-*`` hook any ``sc-gapfill`` rule can
    match. See ``_SC_GAPFILL_HOOKS`` for the load-bearing match form."""
    return any(hook in html for hook in _SC_GAPFILL_HOOKS)


def css_witnesses(html_texts: Iterable[str]) -> frozenset[str]:
    """The style groups whose rules could match anything in the given HTML.

    Deliberately over-inclusive: a false positive wastes a few bytes, a false
    negative would strip a needed style — never allowed. ``unstyled-chrome`` /
    ``images`` / ``tables`` use bare substring probes; ``sc-gapfill`` keys on the
    precise ``_SC_GAPFILL_HOOKS`` (a bare ``"data-sc-"`` probe matched the
    always-present ``data-sc-content="glossary"`` and never shrank). Callers must
    pass STAMPED card HTML (envelopes carry ``data-has-styles`` where their
    dictionary ships CSS): the unstyled-chrome/sc-gapfill witness keys on the
    unstamped envelope, and evaluating it pre-stamp would flip the variant
    between restyle runs and break idempotency.
    """
    html = "".join(html_texts)
    groups = set()
    if UNSTAMPED_ENVELOPE_RE.search(html):
        groups.add("unstyled-chrome")
        if _has_sc_gapfill_hook(html):
            groups.add("sc-gapfill")
    if "gloss-image" in html:
        groups.add("images")
    if "<table" in html or "<details" in html:
        groups.add("tables")
    return frozenset(groups)


def build_card_style_block(*, dict_css: str, card_html: str) -> str:
    """Assemble the self-contained ``<style>`` block for ONE field.

    ``[witness-selected base variant] + [dict_css]``, wrapped in a single
    ``<style>`` element. ``card_html`` is the ONE field's own (stamped)
    miner-markup HTML and drives the tree-shaking; it is a REQUIRED keyword so a
    caller can never silently fall back to an under-styled core. Cross-field
    concatenation (the pre-per-field "glossary AND definition, a ``<style>``
    in any field is card-wide" contract) is deliberately abandoned: every
    writer (mining, backfill, restyle) must witness per field, or their
    outputs diverge and the restyler rewrites fresh cards forever — and
    field-isolating note types (module docstring) never see the other field
    anyway. ``dict_css`` is the already-scoped per-dictionary CSS (filtered to
    this field via ``filter_dict_css_entries``), embedded verbatim. Returns
    ``""`` only if every section is empty (the core is never empty, so in
    practice this always returns a block).
    """
    sections = [base_css_variant(css_witnesses([card_html]))]
    scoped = dict_css.strip()
    if scoped:
        sections.append(scoped)
    body = "\n".join(section for section in sections if section)
    if not body.strip():
        return ""
    return f"<style>{body}</style>"


# Any dictionary envelope title in a field body, stamped or not (contrast
# UNSTAMPED_ENVELOPE_RE, which is deliberately blind to stamped envelopes).
# Used to filter a field's embedded dict CSS down to the dictionaries actually
# present in it. Over-inclusion is safe and accepted: on a restyler body this
# also matches ``[data-dictionary="…"]`` literals inside a carried legacy
# ``<style>``, but such CSS only exists on cards whose envelope is present too,
# and an extra scoped sheet is inert (its selectors match nothing).
_ENVELOPE_TITLE_RE = re.compile(r'data-dictionary="([^"]*)"')

# The miner-markup fingerprint a field must carry before any styling attaches.
# Same probe the restyler uses to recognize miner fields; a field without it
# has nothing our CSS could style, and attaching a block to empty content
# would emit a field-LEADING <style> — the head-hoist hazard (module docstring).
_MINER_MARKUP_TOKENS = ("yomitan-glossary", "data-count")


def filter_dict_css_entries(field_html: str, entries: Iterable[tuple[str, str]]) -> str:
    """Join the scoped CSS of exactly the dictionaries present in ``field_html``.

    ``entries`` is ``collect_dictionary_css_entries`` output: ordered
    ``(display_name, scoped_css)`` pairs, duplicates preserved. Membership is
    tested against the ``html.unescape``d ``data-dictionary`` attribute values
    in the field — the renderer writes that attribute with ``html.escape``
    (``indexed_provider._render``), and ``display_name`` is the pre-escape
    title, so the round-trip is exact. Deliberately NOT ``css_string_escape``
    matching: that escaper diverges from ``html.escape`` on ``&``/quotes, and a
    CSS-selector-form probe (``[data-dictionary="…"]``) never appears in field
    markup at all — either would silently drop a needed stylesheet, the one
    forbidden outcome (Issue #93 discipline: filtering may only trim bytes,
    never styles).
    """
    titles = {html_lib.unescape(title) for title in _ENVELOPE_TITLE_RE.findall(field_html)}
    return "\n\n".join(css for name, css in entries if name in titles)


def attach_card_style_block(field_html: str, *, dict_css_entries: Iterable[tuple[str, str]]) -> str:
    """Return ``field_html`` with its self-contained TRAILING ``<style>`` block.

    The one sanctioned attach seam for fresh writers (mining, backfill); it
    enforces both module-docstring invariants:

    * A field without miner markup (or empty) is returned UNCHANGED — never a
      leading block, never styling on content we don't own.
    * Otherwise the block is appended AFTER the content — trailing survives the
      ``DOMParser`` → ``body.innerHTML`` round-trips of JS note types; leading
      does not.

    The embedded dict CSS is filtered to the dictionaries present in this field
    (``filter_dict_css_entries``) and the base sheet is tree-shaken against this
    field only. NOT for the restyler's no-block path: input here must be born
    stamped (fresh renders are — ``indexed_provider._render``); legacy bodies
    need ``_stamp_styled_envelopes`` first, which this helper deliberately does
    not do (stamping needs the carried-CSS gate only the restyler has).
    """
    if not field_html or any(token not in field_html for token in _MINER_MARKUP_TOKENS):
        return field_html
    block = build_card_style_block(
        dict_css=filter_dict_css_entries(field_html, dict_css_entries),
        card_html=field_html,
    )
    return field_html + block
