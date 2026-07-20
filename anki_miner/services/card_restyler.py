"""Re-apply the latest self-contained glossary styling to already-mined cards.

Mining embeds glossary CSS inside each styled field as a TRAILING ``<style>``
block (``EpisodeProcessor._phase5_create`` → ``attach_card_style_block``;
trailing because a field-leading ``<style>`` is head-hoisted by DOMParser and
dropped from ``body.innerHTML`` round-trips — see the card_style_block module
docstring). Cohorts needing help once the styling format changes and re-mining
is blocked by known-words dedup:

- **Pre-v2.7.8 cards** lack the base stylesheet — either no ``<style>`` at all,
  or (for words matched against a dictionary that shipped a ``styles.css`` in
  v2.7.0–v2.7.7) only a per-dict ``<style>`` — so they render bare. Fixed by
  *appending* a fresh trailing block (after carried-CSS-gated stamping).
- **v2.7.8+ single-carrier cards** carry a LEADING base sheet on one field only
  (glossary when mapped, else definition) — lost on JS note types and absent
  from the other field. Fixed by *migrating* the block to the field tail
  (preserving the card's own ``dict_css`` tail + HTML body byte-for-byte) and
  attaching a fresh block to the other carrier field.
- **Current cards** carry a trailing base sheet per field frozen at their
  mine/restyle time; a later ``glossary.css`` change refreshes it in place.

Every mapped miner field (glossary AND definition) is a styling carrier and is
processed independently with field-only witnesses — the same contract as
mining/backfill, so the three writers converge byte-for-byte and a restyle
re-run is a no-op. Selection is markup-gated and idempotent (see
``restyle_mined_cards``); the write never touches note-type styling and never
re-collects a card's carried dictionary CSS (an uninstalled dict's CSS
survives in the field that carried it — it cannot be conjured for a field
that never carried any, which falls back to current-config CSS).
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from anki_miner.services.definition_service import collect_dictionary_css_entries
from anki_miner.services.dictionary.card_style_block import (
    UNSTAMPED_ENVELOPE_RE,
    base_css_variant,
    css_witnesses,
    filter_dict_css_entries,
)
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
# `">` terminator matches exactly the envelopes that need stamping. The regex
# lives in card_style_block (it doubles as the tree-shaking witness for the
# unstyled-dict style groups) so stamping and witnessing can never drift apart;
# aliased here for the stamper and its tests.
_ENVELOPE_RE = UNSTAMPED_ENVELOPE_RE


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


_STYLE_SPAN_RE = re.compile(r"<style>(.*?)</style>", re.DOTALL)


def _restyle_field(value: str, entries: list[tuple[str, str]], current_dict_css: str) -> str | None:
    """Return the field's target value under the current styling, or ``None``
    when the field must be left untouched (malformed ``<style>`` structure).

    The caller compares against the input for the idempotent no-op. Two paths:

    **Extract/migrate** — the field carries OUR base block somewhere (a
    ``<style>`` whose inner CSS holds the ``ol[data-count]`` token: that
    selector token appears only in our minified base — ``<ol data-count=`` in
    markup is a different string — and never in a legacy per-dict block, so
    ownership can't false-positive; a pre-v2.7.8-migrated card legitimately
    carries TWO ``<style>``s, its own per-dict one inside the envelope plus
    ours). The block is removed from wherever it sits (legacy head or current
    tail), its ``dict_css`` tail (after the first ``\\n`` — the minified base is
    newline-free, pinned in ``card_style_block``) is preserved VERBATIM — never
    re-collected, so an uninstalled dictionary keeps its CSS — and the block is
    re-emitted at the field TAIL with a freshly witness-selected base. Envelope
    stamping is gated on the card's OWN carried CSS (body blocks via
    ``key in value``, the carried tail via the ``dict_css`` membership arg),
    NEVER current config — stamping a config-only-governed envelope would
    switch the base gap-fillers OFF with no card CSS to replace them.

    **Fresh-attach** — no block of ours anywhere (pre-v2.7.8 bare or per-dict
    bodies, or the never-carrier field of a single-carrier card). The body is
    stamped FIRST with today's legacy-prepend gate (carried per-dict ``<style>``
    via ``key in value`` OR current config via ``key in dict_css`` — an
    unstamped body would witness the unstyled-dict groups whose gap-fillers
    out-specify the dict's own scoped CSS, re-opening Issue #87, and would
    break rerun convergence), then a trailing block is appended with
    current-config CSS filtered to the dictionaries present in the stamped
    body (``filter_dict_css_entries`` — the same per-field filter mining and
    backfill use, so all three writers converge byte-for-byte). NOT routed
    through ``attach_card_style_block``: that helper is stampless by contract.

    Both paths witness the STAMPED body of THIS FIELD ONLY — stamping must
    precede the variant computation (the unstyled-dict witness keys on the
    unstamped envelope; witnessing pre-stamp would flip the variant between
    runs), and cross-field witnessing would diverge from mining's per-field
    blocks and rewrite fresh cards forever.
    """
    ours = [m for m in _STYLE_SPAN_RE.finditer(value) if "ol[data-count]" in m.group(1)]
    if len(ours) > 1:
        return None  # two base blocks — hand-edited beyond repair, leave untouched
    if not ours and "ol[data-count]" in value:
        return None  # ownership token outside a well-formed <style> span — malformed
    if ours:
        match = ours[0]
        body = value[: match.start()] + value[match.end() :]
        carried_tail = match.group(1).partition("\n")[2]  # "" when no dict_css tail
        stamped = _stamp_styled_envelopes(body, carried_tail)
        dict_css = carried_tail
    else:
        stamped = _stamp_styled_envelopes(value, current_dict_css)
        dict_css = filter_dict_css_entries(stamped, entries)
    new_base = base_css_variant(css_witnesses([stamped]))
    if "\n" in new_base:
        return None  # broken newline invariant — refuse to partition mid-base
    inner = f"{new_base}\n{dict_css}" if dict_css else new_base
    return f"{stamped}<style>{inner}</style>"


@dataclass(frozen=True)
class RestyleResult:
    """Outcome of a restyle run. ``scanned`` counts notes examined.

    ``restyled`` counts both prepended and in-place-refreshed cards (they share one
    update batch). Aggregation is PER NOTE so the counters always sum to
    ``scanned``: a note is ``restyled`` if ANY carrier field changed, else
    ``skipped_styled`` if any carrier field had miner markup (already current,
    or a non-conforming ``<style>`` structure left untouched), else
    ``skipped_no_markup`` (non-miner note). A changed note whose write was not
    confirmed is ``failed``. These four outcome counters sum to ``scanned``.
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
    miner markup (``yomitan-glossary`` and ``data-count``) goes through
    ``_restyle_field``: our base block (wherever it sits — legacy leading head
    or current tail) migrates/refreshes to a tail block with the carried
    ``dict_css`` preserved verbatim, and a field that never carried a block
    (pre-v2.7.8 bodies, or the non-carrier field of a single-carrier-era card)
    gets a fresh one from current-config CSS. Changed fields of one note are
    written in a single ``update_notes_fields`` entry.

    Idempotent: a re-run recomputes byte-identical values and counts the note
    ``skipped_styled``. Never removes card content, never writes note-type
    styling, never re-collects a card's carried dictionary CSS. Genuine
    Yomitan-exported cards (which lack ``data-count``) are left untouched.

    This run is the migration path for both prior formats: pre-#93 9KB heads
    shrink to the field's slim variant, and single-carrier leading blocks move
    to per-field tails (the Kiku fix — a leading ``<style>`` is head-hoisted
    and lost in ``body.innerHTML`` round-trips, and a block in one field never
    styles another field on JS note types).
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
    current_dict_css = "\n\n".join(css for _, css in entries)
    # No empty-block guard needed: base_css_variant itself raises if a variant
    # ever loses the newline-free/ol[data-count] contract (fail-loud beats the
    # old silent every-run-restyle failure mode).

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
                new_value = _restyle_field(value, entries, current_dict_css)
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
