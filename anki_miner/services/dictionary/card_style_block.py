"""Self-contained per-card glossary ``<style>`` block (Yomitan model).

Anki Miner emits each card's glossary CSS *inside the card's own field* as one
``<style>`` block, so styling travels with the note — it works on any note type,
on AnkiDroid/mobile, in exports, and when a card is shared, and nothing can strip
or de-sync it. This is how Yomitan delivers glossary CSS (self-contained field
HTML), and it replaces the shared note-type CSS block that the v2.7.6 rework
introduced (Anki Miner no longer writes note-type styling at all).

The block is ``[universal glossary.css] + [scoped per-dictionary CSS]`` (base →
dict-author, following the Yomitan ``_getCustomCss`` ordering). The base sheet is
minified on embed (comments + whitespace stripped) since it ships on every
glossary-bearing card; only the base — which we author — is minified, so a
dictionary's own CSS is embedded verbatim.

Pure string transform: no Qt, no HTTP, no disk I/O beyond ``load_glossary_css``'s
bundled read. The scoped per-dictionary CSS is gathered separately by
``definition_service.collect_dictionary_css`` and passed in as ``dict_css``.
"""

from __future__ import annotations

import re

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


def build_card_style_block(*, dict_css: str) -> str:
    """Assemble the self-contained per-card ``<style>`` block.

    ``[minified base glossary.css] + [dict_css]``, wrapped in a single ``<style>``
    element for embedding at the top of a card's glossary field. ``dict_css`` is
    the already-scoped, already-concatenated per-dictionary CSS (from
    ``collect_dictionary_css``), embedded verbatim. Returns ``""`` only if every
    section is empty (the bundled base is never empty, so in practice this always
    returns a block).
    """
    sections = [_minify_css(load_glossary_css())]
    scoped = dict_css.strip()
    if scoped:
        sections.append(scoped)
    body = "\n".join(section for section in sections if section)
    if not body.strip():
        return ""
    return f"<style>{body}</style>"
