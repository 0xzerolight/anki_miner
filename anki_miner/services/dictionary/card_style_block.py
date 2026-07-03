"""Self-contained per-card glossary ``<style>`` block (Yomitan model).

Anki Miner emits each card's glossary CSS *inside the card's own field* as one
``<style>`` block, so styling travels with the note — it works on any note type,
on AnkiDroid/mobile, in exports, and when a card is shared, and nothing can strip
or de-sync it. This is how Yomitan delivers glossary CSS (self-contained field
HTML), and it replaces the shared note-type CSS block that the v2.7.6 rework
introduced (Anki Miner no longer writes note-type styling at all).

The block is ``[universal glossary.css] + [scoped per-dictionary CSS] + [user
custom CSS]`` (base → dict-author → user, the Yomitan ``_getCustomCss`` ordering).
The base sheet is minified on embed (comments + whitespace stripped) since it
ships on every glossary-bearing card; only the base — which we author — is
minified, so a dictionary's or the user's own CSS is embedded verbatim.

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
_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_WS_RE = re.compile(r"\s+")
_DELIM_RE = re.compile(r"\s*([{};,])\s*")


def _minify_css(css: str) -> str:
    css = _COMMENT_RE.sub("", css)
    css = _WS_RE.sub(" ", css)
    css = _DELIM_RE.sub(r"\1", css)
    return css.strip()


def build_card_style_block(*, custom_css: str, dict_css: str) -> str:
    """Assemble the self-contained per-card ``<style>`` block.

    ``[minified base glossary.css] + [dict_css] + [custom_css]``, wrapped in a
    single ``<style>`` element for embedding at the top of a card's glossary
    field. ``dict_css`` is the already-scoped, already-concatenated per-dictionary
    CSS (from ``collect_dictionary_css``); ``custom_css`` is the user's
    ``custom_card_css``. Both are embedded verbatim. Returns ``""`` only if every
    section is empty (the bundled base is never empty, so in practice this always
    returns a block).
    """
    sections = [_minify_css(load_glossary_css())]
    scoped = dict_css.strip()
    if scoped:
        sections.append(scoped)
    custom = custom_css.strip()
    if custom:
        sections.append(custom)
    body = "\n".join(section for section in sections if section)
    if not body.strip():
        return ""
    return f"<style>{body}</style>"
