"""Re-apply the latest self-contained glossary styling to already-mined cards.

Mining embeds glossary CSS inside each styled field as a TRAILING ``<style>``
block (``EpisodeProcessor._phase5_create`` → ``attach_card_style_block``;
trailing because a field-leading ``<style>`` is head-hoisted by DOMParser and
dropped from ``body.innerHTML`` round-trips — see the card_style_block module
docstring). Restyle brings a field's block up to date when re-mining is blocked
by known-words dedup:

- **Current cards** carry a trailing base sheet per field frozen at their
  mine/restyle time; a later ``glossary.css`` change refreshes it in place.
- **v2.7.8+ single-carrier cards** carry a LEADING base sheet on one field only;
  the block migrates to the field tail (preserving the card's own ``dict_css``
  tail + HTML body byte-for-byte) and the other carrier field gains a fresh one.
- **Fields with no block** (bare or per-dict-``<style>`` bodies) gain a fresh
  trailing block.

**Legacy-markup fields are left untouched** (``is_legacy_markup``: tag chips or
the ``<ul class="gloss-list">`` sense list of cards mined before the clean
glossary shape). The current sheet styles neither, and the restyler never
rewrites card bodies, so swapping their old embedded sheet would strip their
styling. Such a note counts as ``skipped_styled``; Card Backfill (overwrite
Definitions/Glossary) is how the new look reaches it.

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

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from anki_miner.services.definition_service import collect_dictionary_css_entries
from anki_miner.services.dictionary.card_style_block import (
    OWNED_STYLE_MARKER,
    base_css,
    filter_dict_css_entries,
)

if TYPE_CHECKING:
    from anki_miner.config import AnkiMinerConfig
    from anki_miner.services.anki_service import AnkiService

# Anki search escaping for a note-type name (mirrors AnkiService._build_vocab_query):
# backslash, quote, and Anki's ``*``/``_`` glob metacharacters, so a name like
# ``Core_2k`` matches literally instead of as a wildcard.
_QUERY_ESCAPES = (("\\", "\\\\"), ('"', '\\"'), ("*", "\\*"), ("_", "\\_"))

_CHUNK = 500

_LEGACY_OWNED_STYLE_MARKER = ".yomitan-glossary{--am-muted: rgba(128,128,128,0.95);"


def is_legacy_markup(body: str) -> bool:
    """Whether a miner field carries markup from before the clean glossary shape.

    Tag chips (``class="gloss-tag"``) and the ``<ul class="gloss-list">`` sense
    list were rendered by every earlier version and by none since; the current
    sheet styles neither.
    """
    return 'class="gloss-tag"' in body or '<ul class="gloss-list"' in body


_STYLE_SPAN_RE = re.compile(r"<style>(.*?)</style>", re.DOTALL)


def _restyle_field(value: str, entries: list[tuple[str, str, str]]) -> str | None:
    """Return the field's target value under the current styling, or ``None``
    when the field must be left untouched (malformed ``<style>`` structure).

    The caller compares against the input for the idempotent no-op. Two paths:

    **Extract/migrate** — the field carries OUR base block somewhere (a
    ``<style>`` whose inner CSS opens with the owned-style marker, or the older
    ``--am-muted`` literal; a pre-v2.7.8-migrated card legitimately carries TWO
    ``<style>``s, its own per-dict one inside the envelope plus ours). The block
    is removed from wherever it sits (legacy head or current tail), its
    ``dict_css`` tail (after the first ``\\n`` — the minified base is
    newline-free, pinned in ``card_style_block``) is preserved VERBATIM — never
    re-collected, so an uninstalled dictionary keeps its CSS — and the block is
    re-emitted at the field TAIL with a fresh base.

    **Fresh-attach** — no block of ours anywhere (bare or per-dict bodies, or
    the never-carrier field of a single-carrier card). A trailing block is
    appended with current-config CSS filtered to the dictionaries present in
    the body (``filter_dict_css_entries`` — the same per-field filter mining and
    backfill use, so all three writers converge byte-for-byte).

    Both paths pick the base from THIS FIELD's body only (``base_css``) and
    return the body byte-for-byte: cross-field selection would diverge from
    mining's per-field blocks and rewrite fresh cards forever.
    """
    markers = (OWNED_STYLE_MARKER, _LEGACY_OWNED_STYLE_MARKER)
    ours = [m for m in _STYLE_SPAN_RE.finditer(value) if m.group(1).startswith(markers)]
    if len(ours) > 1:
        return None  # two base blocks — hand-edited beyond repair, leave untouched
    if not ours and any(marker in value for marker in markers):
        return None  # ownership token outside a well-formed <style> span — malformed
    if ours:
        match = ours[0]
        body = value[: match.start()] + value[match.end() :]
        dict_css = match.group(1).partition("\n")[2]  # "" when no dict_css tail
    else:
        body = value
        dict_css = filter_dict_css_entries(body, entries)
    new_base = base_css(body)
    if "\n" in new_base:
        return None  # broken newline invariant — refuse to partition mid-base
    inner = f"{new_base}\n{dict_css}" if dict_css else new_base
    return f"{body}<style>{inner}</style>"


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
    """Re-apply the current self-contained trailing ``<style>`` block to miner
    cards, on EVERY mapped carrier field independently.

    Both the glossary and the definition fields are styling carriers when
    mapped — matching fresh mining, which attaches a per-field trailing block
    to each (``EpisodeProcessor._phase5_create``). Each carrier field with
    miner markup (``yomitan-glossary`` and ``data-count``) and no legacy markup
    (``is_legacy_markup``) goes through ``_restyle_field``: our base block
    (wherever it sits — legacy leading head or current tail) migrates/refreshes
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

    note_ids = anki_service.find_notes(f'note:"{_escape_note_type(config.anki_note_type)}"')
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
