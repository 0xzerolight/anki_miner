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

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from anki_miner.services.definition_service import collect_dictionary_css
from anki_miner.services.dictionary.card_style_block import build_card_style_block

if TYPE_CHECKING:
    from anki_miner.config import AnkiMinerConfig
    from anki_miner.services.anki_service import AnkiService

# Anki search escaping for a note-type name (mirrors AnkiService._build_vocab_query):
# backslash, quote, and Anki's ``*``/``_`` glob metacharacters, so a name like
# ``Core_2k`` matches literally instead of as a wildcard.
_QUERY_ESCAPES = (("\\", "\\\\"), ('"', '\\"'), ("*", "\\*"), ("_", "\\_"))

_CHUNK = 500


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

    Restyles a note's glossary field when it carries the miner markup
    (``yomitan-glossary`` and ``data-count``) but not the base sheet
    (``ol[data-count]`` — a CSS-selector token that appears only inside our
    minified base ``<style>``, never in card markup or a legacy per-dict block).
    Idempotent: a restyled card then contains ``ol[data-count]`` so a re-run skips
    it. Additive: only prepends; never removes card content, never writes
    note-type styling. Genuine Yomitan-exported cards (which lack ``data-count``)
    are left untouched.
    """
    glossary_field = config.anki_fields.get("glossary")
    if not glossary_field:
        return RestyleResult(0, 0, 0, 0)

    block = build_card_style_block(custom_css=config.custom_card_css, dict_css=collect_dictionary_css(config))
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
            entry = fields.get(glossary_field)
            if not isinstance(entry, dict):
                scanned += 1
                continue  # glossary field absent on this note type instance
            value = entry.get("value", "") or ""
            scanned += 1
            if "yomitan-glossary" not in value or "data-count" not in value:
                skipped_no_markup += 1
                continue
            if "ol[data-count]" in value:
                skipped_styled += 1
                continue
            updates.append((note_id, {glossary_field: block + value}))
        if updates:
            restyled += anki_service.update_notes_fields(updates)
        if progress:
            progress(scanned, len(note_ids))

    return RestyleResult(scanned, restyled, skipped_styled, skipped_no_markup)
