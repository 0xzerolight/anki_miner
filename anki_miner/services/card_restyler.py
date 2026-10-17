"""Re-apply the latest self-contained glossary styling to already-mined cards.

Mining embeds glossary CSS inside each styled field as a TRAILING ``<style>``
block (``EpisodeProcessor._phase5_create`` → ``attach_card_style_block``;
trailing because a field-leading ``<style>`` is head-hoisted by DOMParser and
dropped from ``body.innerHTML`` round-trips — see the card_style_block module
docstring). Restyle brings a field's block up to date when re-mining is blocked
by known-words dedup. Every path below applies only to a body WITHOUT legacy
markup:

- **Current cards** carry a trailing base sheet per field frozen at their
  mine/restyle time; a later ``glossary.css`` change refreshes it in place.
- **A LEADING base sheet** (the single-carrier placement, on one field only)
  migrates to the field tail (preserving the card's own ``dict_css`` tail +
  HTML body byte-for-byte) and the other carrier field gains a fresh one.
- **Fields with no block** (bare or per-dict-``<style>`` bodies) gain a fresh
  trailing block.

**Legacy-markup fields are left untouched** (``is_legacy_markup``: tag chips or
the ``<ul class="gloss-list">`` sense list of cards mined before the clean
glossary shape), whatever block they carry, leading or trailing. The current
sheet styles neither, and the restyler never rewrites card bodies, so swapping
their old embedded sheet would strip their styling. Such a field is never
written, and its note counts as ``skipped_styled`` unless another of its
carrier fields changed; Card Backfill (overwrite Definitions/Glossary) is how
the new look reaches it. Real single-carrier and per-dict cards were all mined
before the clean shape, so they land here: the paths above serve
current-markup bodies.

Every mapped miner field (glossary AND definition) is a styling carrier and is
processed independently against its own markup — the same contract as
mining/backfill, so the three writers converge byte-for-byte and a restyle
re-run is a no-op. Selection is markup-gated and idempotent (see
``restyle_mined_cards``); the write never touches note-type styling and never
re-collects a card's carried dictionary CSS (an uninstalled dict's CSS
survives in the field that carried it — it cannot be conjured for a field
that never carried any, which falls back to current-config CSS).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from anki_miner.services._ankiconnect import escape_anki_search
from anki_miner.services.definition_service import collect_dictionary_css_entries
from anki_miner.services.dictionary.card_style_block import is_legacy_markup, refresh_style_block

if TYPE_CHECKING:
    from anki_miner.config import AnkiMinerConfig
    from anki_miner.services.anki_service import AnkiService

__all__ = ["RestyleResult", "is_legacy_markup", "restyle_mined_cards"]

_CHUNK = 500


def _restyle_field(value: str, entries: list[tuple[str, str, str]]) -> str | None:
    """The field's target value under the current styling (``refresh_style_block``)."""
    return refresh_style_block(value, entries)


@dataclass(frozen=True)
class RestyleResult:
    """Outcome of a restyle run. ``scanned`` counts notes examined.

    ``restyled`` counts migrated, fresh-attached and in-place-refreshed cards
    (they share one update batch). Aggregation is PER NOTE so the counters
    always sum to ``scanned``: a note is ``restyled`` if ANY carrier field
    changed, else ``skipped_styled`` if any carrier field had miner markup
    (already current, legacy markup left untouched — see
    ``is_legacy_markup`` — or a non-conforming ``<style>`` structure left
    untouched), else ``skipped_no_markup`` (non-miner note). A changed note
    whose write was not confirmed is ``failed``. These four outcome counters
    sum to ``scanned``.
    """

    scanned: int
    restyled: int
    skipped_styled: int
    skipped_no_markup: int
    failed: int = 0


def _chunks(items: list[int], size: int) -> Iterator[list[int]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def restyle_mined_cards(
    anki_service: AnkiService,
    config: AnkiMinerConfig,
    *,
    progress: Callable[[int, int], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> RestyleResult:
    """Re-apply the current self-contained trailing ``<style>`` block to miner
    cards, on EVERY mapped carrier field independently.

    Both the glossary and the definition fields are styling carriers when
    mapped — matching fresh mining, which attaches a per-field trailing block
    to each (``EpisodeProcessor._phase5_create``). Each carrier field with
    miner markup (``yomitan-glossary`` and ``data-count``) and no legacy markup
    (``is_legacy_markup``) goes through ``_restyle_field``: our base block
    (wherever it sits — a leading or a trailing block) migrates/refreshes
    to a tail block with the carried ``dict_css`` preserved verbatim, and a
    field that never carried a block gets a fresh one from current-config CSS.
    A legacy-markup field is left as it is. Changed fields of one note are
    written in a single ``update_notes_fields`` entry.

    Idempotent: a re-run recomputes byte-identical values and counts the note
    ``skipped_styled``. Never removes or rewrites card content, never writes
    note-type styling, never re-collects a card's carried dictionary CSS.
    Genuine Yomitan-exported cards (which lack ``data-count``) are left
    untouched.
    """
    glossary_field = config.anki_fields.get("glossary")
    definition_field = config.anki_fields.get("definition")
    # Ordered, deduped carrier list (both mapped to the SAME field name is a
    # degenerate config — process it once).
    carrier_fields = [f for f in (glossary_field, definition_field) if f]
    carrier_fields = list(dict.fromkeys(carrier_fields))
    if not carrier_fields:
        return RestyleResult(0, 0, 0, 0)

    entries = collect_dictionary_css_entries(config)
    # No empty-block guard needed: base_css fails loudly (card_style_block
    # _minified_regions) if the sheet ever loses the newline-free/ol[data-count]
    # contract — fail-loud beats a silent every-run-restyle failure mode.

    note_ids = anki_service.find_notes(f'note:"{escape_anki_search(config.anki_note_type)}"')
    scanned = restyled = skipped_styled = skipped_no_markup = failed = 0

    for chunk in _chunks(note_ids, _CHUNK):
        if is_cancelled and is_cancelled():
            break
        updates: list[tuple[int, dict[str, str]]] = []
        for info in anki_service.notes_info(chunk):
            note_id = info.get("noteId")
            fields = info.get("fields")
            if not isinstance(note_id, int) or not isinstance(fields, dict):
                continue  # deleted ({}) / malformed
            scanned += 1
            changed: dict[str, str] = {}
            had_markup = False
            for field_name in carrier_fields:
                entry = fields.get(field_name)
                if not isinstance(entry, dict):
                    continue  # field absent on this note type instance
                value = entry.get("value", "") or ""
                if "yomitan-glossary" not in value or "data-count" not in value:
                    continue
                had_markup = True
                if is_legacy_markup(value):
                    continue  # left untouched; Card Backfill brings the new shape
                new_value = _restyle_field(value, entries)
                if new_value is not None and new_value != value:
                    changed[field_name] = new_value
            if changed:
                updates.append((note_id, changed))
            elif had_markup:
                skipped_styled += 1
            else:
                skipped_no_markup += 1
        if updates:
            successful_id_set = set(anki_service.update_notes_fields(updates))
            confirmed = sum(1 for note_id, _fields in updates if note_id in successful_id_set)
            restyled += confirmed
            failed += len(updates) - confirmed
        if progress:
            progress(scanned, len(note_ids))

    return RestyleResult(scanned, restyled, skipped_styled, skipped_no_markup, failed)
