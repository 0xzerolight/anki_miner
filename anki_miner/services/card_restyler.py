"""One-time retroactive restyle of already-mined cards to the self-contained model.

v2.7.8 embeds glossary CSS inside each card at mining time
(``EpisodeProcessor._phase5_create``). Cards mined earlier lack the base
stylesheet in their glossary field — either no ``<style>`` at all, or (for words
matched against a dictionary that shipped a ``styles.css`` in v2.7.0–v2.7.7) only
a per-dict ``<style>`` with no base layout — so they render bare once the old
shared note-type block is gone. Re-mining is blocked by known-words dedup, so
this rewrites those cards' glossary field in place to prepend the same
self-contained block ``build_card_style_block`` emits at mining time.

Selection is markup-gated and idempotent (see ``restyle_mined_cards``); the write
is purely additive (prepend only) and never touches note-type styling.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from anki_miner.services.definition_service import collect_dictionary_css
from anki_miner.services.dictionary.card_style_block import build_card_style_block
from anki_miner.services.dictionary.dict_css_scope import css_string_escape

if TYPE_CHECKING:
    from anki_miner.config import AnkiMinerConfig
    from anki_miner.services.anki_service import AnkiService

# Anki search escaping for a note-type name (mirrors AnkiService._build_vocab_query):
# backslash, quote, and Anki's ``*``/``_`` glob metacharacters, so a name like
# ``Core_2k`` matches literally instead of as a wildcard.
_QUERY_ESCAPES = (("\\", "\\\\"), ('"', '\\"'), ("*", "\\*"), ("_", "\\_"))

_CHUNK = 500

# A legacy card body's dictionary envelopes, as the renderer has always emitted
# them: `<li data-dictionary="TITLE">` with an html.escape'd title and no other
# attributes. Older bodies never carry the data-has-styles stamp, so the plain
# `">` terminator matches exactly the envelopes that need stamping.
_ENVELOPE_RE = re.compile(r'<li data-dictionary="([^"]*)">')


def _stamp_styled_envelopes(value: str, dict_css: str) -> str:
    """Stamp ``data-has-styles`` on legacy envelopes whose dictionary has scoped CSS.

    Fresh mining stamps the ``<li data-dictionary>`` envelope at render time
    (``IndexedDictProvider._render``) so the base sheet's gated data-sc-*
    gap-fillers stay off entries governed by their dictionary's own styles.css.
    Legacy bodies predate the stamp, and the restyler prepends a base sheet
    whose gap-fillers would out-specify the scoped dictionary CSS — reproducing
    the very bug the gate fixes — so the envelope must be stamped here too.

    An envelope is stamped iff its dictionary's scoped CSS is present in either
    stylesheet that will govern the restyled card: the body's own embedded
    per-dict ``<style>`` (covers renamed/uninstalled dictionaries) or the
    ``dict_css`` about to be prepended (the restyler injects current-config CSS,
    so its titles gate too; over-stamping is impossible — the block always
    carries the matching scoped CSS for any title it contributes). Only those two
    stylesheets are matched: a stray ``[data-dictionary="D"]`` selector baked into
    a legacy card body (e.g. an older manual edit) is a benign accepted edge, not
    a gate for dict D.

    The membership key re-derives the scoped-CSS selector prefix from the
    envelope attribute via the same forward escaper ``scope_dict_css`` uses
    (``html.unescape`` → ``css_string_escape``), so matching is exact for real
    titles and consistent even on the escaper's lossy ``<``/``>``-stripping
    path. ``re.sub`` with a replacement function is offset-safe for multi-dict
    bodies; already-stamped envelopes don't match the pattern (their ``<li``
    carries a second attribute), so the rewrite is idempotent.
    """

    def _stamp(match: re.Match[str]) -> str:
        title = html.unescape(match.group(1))
        key = f'[data-dictionary="{css_string_escape(title)}"]'
        if key in value or key in dict_css:
            return f'<li data-dictionary="{match.group(1)}" data-has-styles="">'
        return match.group(0)

    return _ENVELOPE_RE.sub(_stamp, value)


@dataclass(frozen=True)
class RestyleResult:
    """Outcome of a restyle run. ``scanned`` counts notes examined."""

    scanned: int
    restyled: int
    skipped_styled: int
    skipped_no_markup: int


def _chunks(items: list[int], size: int) -> Iterator[list[int]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _escape_note_type(name: str) -> str:
    for old, new in _QUERY_ESCAPES:
        name = name.replace(old, new)
    return name


def restyle_mined_cards(
    anki_service: AnkiService,
    config: AnkiMinerConfig,
    *,
    progress: Callable[[int, int], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> RestyleResult:
    """Prepend the self-contained ``<style>`` block to miner cards that lack the base sheet.

    Restyles a note's styling-carrier field when it carries the miner markup
    (``yomitan-glossary`` and ``data-count``) but not the base sheet
    (``ol[data-count]`` — a CSS-selector token that appears only inside our
    minified base ``<style>``, never in card markup or a legacy per-dict block).
    The carrier field is the glossary field when mapped, else the definition field
    — matching where fresh mining now attaches the block
    (``EpisodeProcessor._phase5_create``), so a default-config user (definition
    mapped, glossary unmapped) still gets old cards restyled. Idempotent: a
    restyled card then contains ``ol[data-count]`` so a re-run skips it. Additive:
    only prepends; never removes card content, never writes note-type styling.
    Genuine Yomitan-exported cards (which lack ``data-count``) are left untouched.
    """
    styling_field = config.anki_fields.get("glossary") or config.anki_fields.get("definition")
    if not styling_field:
        return RestyleResult(0, 0, 0, 0)

    dict_css = collect_dictionary_css(config)
    block = build_card_style_block(dict_css=dict_css)
    if not block.startswith("<style"):
        # Defensive: the bundled base is never empty, but an empty block would
        # otherwise "restyle" every card on every run (no ol[data-count] added).
        return RestyleResult(0, 0, 0, 0)

    note_ids = anki_service.find_notes(f'note:"{_escape_note_type(config.anki_note_type)}"')
    scanned = restyled = skipped_styled = skipped_no_markup = 0

    for chunk in _chunks(note_ids, _CHUNK):
        if is_cancelled and is_cancelled():
            break
        updates: list[tuple[int, dict[str, str]]] = []
        for info in anki_service.notes_info(chunk):
            note_id = info.get("noteId")
            fields = info.get("fields")
            if not isinstance(note_id, int) or not isinstance(fields, dict):
                continue  # deleted ({}) / malformed
            entry = fields.get(styling_field)
            if not isinstance(entry, dict):
                scanned += 1
                continue  # styling field absent on this note type instance
            value = entry.get("value", "") or ""
            scanned += 1
            if "yomitan-glossary" not in value or "data-count" not in value:
                skipped_no_markup += 1
                continue
            if "ol[data-count]" in value:
                skipped_styled += 1
                continue
            updates.append((note_id, {styling_field: block + _stamp_styled_envelopes(value, dict_css)}))
        if updates:
            restyled += anki_service.update_notes_fields(updates)
        if progress:
            progress(scanned, len(note_ids))

    return RestyleResult(scanned, restyled, skipped_styled, skipped_no_markup)
