"""Re-apply the latest self-contained glossary styling to already-mined cards.

v2.7.8 embeds glossary CSS inside each card at mining time
(``EpisodeProcessor._phase5_create``). Two cohorts need help once the styling
changes and re-mining is blocked by known-words dedup:

- **Pre-v2.7.8 cards** lack the base stylesheet — either no ``<style>`` at all,
  or (for words matched against a dictionary that shipped a ``styles.css`` in
  v2.7.0–v2.7.7) only a per-dict ``<style>`` — so they render bare. These are
  fixed by *prepending* the current self-contained block.
- **v2.7.8+ cards** already carry a base sheet frozen at their mine/restyle time,
  so a later ``glossary.css`` change never reaches them. These are fixed by
  *refreshing the embedded base head in place* (``_refresh_base_sheet``),
  preserving the card's own ``dict_css`` tail + HTML body byte-for-byte.

Selection is markup-gated and idempotent (see ``restyle_mined_cards``); the write
never touches note-type styling and never re-collects a card's dictionary CSS.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from anki_miner.services.definition_service import collect_dictionary_css
from anki_miner.services.dictionary.card_style_block import (
    UNSTAMPED_ENVELOPE_RE,
    base_css_variant,
    build_card_style_block,
    css_witnesses,
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


def _refresh_base_sheet(value: str, other_html: str, base_for_html: Callable[[str], str]) -> str | None:
    """Swap a stale embedded base head for this card's tree-shaken variant in
    place, preserving the card's OWN ``dict_css`` tail + HTML body byte-for-byte.

    Returns ``None`` when the field is not a base head we own, is malformed, or is
    already current (idempotent no-op) — the caller then counts it ``skipped_styled``.

    A card carrying the base sheet (mined/restyled at or after v2.7.8) stores its
    styling field as ``<style>{base}\\n{dict_css}</style>{body}`` — the base minified
    (newline-free, carrying ``ol[data-count]``), then the verbatim per-dict
    ``dict_css`` (may be multi-line), then the HTML body (which, for a card restyled
    up from a pre-v2.7.8 per-dict card, may carry its own trailing ``<style>``). The
    minified base is newline-free (pinned in ``card_style_block``), so the FIRST
    ``\\n`` in the head is exactly the base/``dict_css`` boundary.

    The new base is per-card since the Issue #93 tree-shaking: ``base_for_html``
    maps the card's stamped HTML (this field's body + ``other_html``, the note's
    other miner field) to its witness-selected variant. Stamping MUST precede
    the variant computation — the unstyled-dict witness keys on the unstamped
    envelope, so witnessing the pre-stamp body would flip the variant between
    runs and break idempotency.

    ``dict_css`` is preserved VERBATIM from the card — never re-collected — so an
    uninstalled dictionary keeps its CSS. Envelope stamping is gated on the card's
    OWN carried CSS, NEVER current config — stamping a config-only-governed
    envelope would switch the base gap-fillers OFF with no card CSS to replace
    them, degrading the card. Carried CSS means the head ``dict_css`` tail plus
    any body ``<style>``: the body is the stamp's value (so its own blocks gate)
    and ``old_dict_css`` is passed as the membership source (so the head tail
    gates too — the pre-#93 whole-value stamp saw it via the reassembled head,
    and the base itself carries no ``[data-dictionary=`` keys, pinned by
    ``test_minified_base_has_no_data_dictionary_literals``).
    """
    if not value.startswith("<style>"):
        return None
    close = value.find("</style>")
    if close == -1:
        return None  # truncated/hand-edited head — leave untouched, never raise
    inner = value[len("<style>") : close]
    if "ol[data-count]" not in inner:
        return None  # base token only in a LATER block — this head isn't ours
    rest = value[close + len("</style>") :]
    old_dict_css = inner.partition("\n")[2]  # "" when the head had no dict_css tail
    stamped_rest = _stamp_styled_envelopes(rest, old_dict_css)
    new_base = base_for_html(stamped_rest + other_html)
    if "\n" in new_base:
        return None  # broken newline invariant — refuse to partition mid-base
    new_inner = f"{new_base}\n{old_dict_css}" if old_dict_css else new_base
    new_value = f"<style>{new_inner}</style>{stamped_rest}"
    if new_value == value:
        return None  # base current AND body already stamped — idempotent no-op
    return new_value


@dataclass(frozen=True)
class RestyleResult:
    """Outcome of a restyle run. ``scanned`` counts notes examined.

    ``restyled`` counts both prepended and in-place-refreshed cards (they share one
    update batch). ``skipped_styled`` counts cards that already carry the current
    base sheet (the common re-run no-op) or a non-conforming base head left
    untouched. ``skipped_no_markup`` counts non-miner cards (no ``data-count``).
    """

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
    """Re-apply the current self-contained ``<style>`` block to miner cards.

    Operates on a note's styling-carrier field when it carries the miner markup
    (``yomitan-glossary`` and ``data-count``). The carrier field is the glossary
    field when mapped, else the definition field — matching where fresh mining
    attaches the block (``EpisodeProcessor._phase5_create``), so a default-config
    user (definition mapped, glossary unmapped) is covered. Two paths:

    - No base sheet yet (``ol[data-count]`` absent — a CSS-selector token that
      appears only inside our minified base ``<style>``, never in card markup or a
      legacy per-dict block): *prepend* the current block.
    - Base sheet present: *refresh* its base head in place (``_refresh_base_sheet``),
      preserving the card's own ``dict_css`` tail + HTML body.

    Idempotent: a re-run finds the base already current and counts it
    ``skipped_styled``. Never removes card content, never writes note-type styling,
    never re-collects a card's dictionary CSS. Genuine Yomitan-exported cards (which
    lack ``data-count``) are left untouched.

    Since the Issue #93 tree-shaking the embedded base is per-card: both paths
    stamp the body FIRST, then select the variant from the stamped body plus the
    note's other miner field (the ``<style>`` is card-wide, so witnesses from
    either field count). This run is also the shrink/migration for pre-shaking
    cards — their full 9KB head refreshes down to the card's slim variant with
    the ``dict_css`` tail and body preserved byte-for-byte.
    """
    glossary_field = config.anki_fields.get("glossary")
    definition_field = config.anki_fields.get("definition")
    styling_field = glossary_field or definition_field
    if not styling_field:
        return RestyleResult(0, 0, 0, 0)
    other_field = definition_field if styling_field == glossary_field else None

    dict_css = collect_dictionary_css(config)
    if not build_card_style_block(dict_css=dict_css, card_html="").startswith("<style"):
        # Defensive: the bundled core is never empty, but an empty block would
        # otherwise "restyle" every card on every run (no ol[data-count] added).
        return RestyleResult(0, 0, 0, 0)

    def base_for_html(html: str) -> str:
        return base_css_variant(css_witnesses([html]))

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
            other_entry = fields.get(other_field) if other_field and other_field != styling_field else None
            other_html = (other_entry.get("value", "") or "") if isinstance(other_entry, dict) else ""
            if "ol[data-count]" in value:
                # Already carries a base sheet (mined/restyled at/after v2.7.8):
                # refresh its base head in place (preserving the card's own dict_css
                # + body) rather than skipping, so a glossary.css change reaches
                # existing cards. None = not ours / malformed / already current.
                refreshed = _refresh_base_sheet(value, other_html, base_for_html)
                if refreshed is None:
                    skipped_styled += 1
                else:
                    updates.append((note_id, {styling_field: refreshed}))
                continue
            # Legacy prepend: stamp first, then tree-shake against the stamped
            # body — witnessing the unstamped body would over-include the
            # unstyled-dict groups and never converge with the refresh path.
            stamped = _stamp_styled_envelopes(value, dict_css)
            block = build_card_style_block(dict_css=dict_css, card_html=stamped + other_html)
            updates.append((note_id, {styling_field: block + stamped}))
        if updates:
            restyled += anki_service.update_notes_fields(updates)
        if progress:
            progress(scanned, len(note_ids))

    return RestyleResult(scanned, restyled, skipped_styled, skipped_no_markup)
