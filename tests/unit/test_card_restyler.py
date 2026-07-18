"""Tests for the Restyle Mined Cards service (card_restyler).

Format under test: every mapped miner field carries its own TRAILING
``<style>`` block (per-field self-containment; a leading block is head-hoisted
by DOMParser on JS note types and lost). The restyler migrates every historical
format to it: pre-v2.7.8 bare bodies, v2.7.0–2.7.7 per-dict-``<style>`` bodies,
and v2.7.8+ single-carrier LEADING blocks.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from anki_miner.config import create_default_config
from anki_miner.services import card_restyler
from anki_miner.services.card_restyler import RestyleResult, restyle_mined_cards
from anki_miner.services.dictionary.card_style_block import (
    attach_card_style_block,
    base_css_variant,
    css_witnesses,
)


def _variant_for(html: str) -> str:
    """The tree-shaken base head the restyler must embed for this (stamped) HTML."""
    return base_css_variant(css_witnesses([html]))


# A miner card mined bare (markup, no <style>).
BARE = '<div class="yomitan-glossary"><ol data-count="1"><li data-dictionary="X">x</li></ol></div>'
BARE_STAMPED = BARE.replace('<li data-dictionary="X">', '<li data-dictionary="X" data-has-styles="">')
# A v2.7.0–v2.7.7 dict-styled card: per-dict <style> (scoped, no base sheet) — the blocker case.
# The envelope carries data-dictionary like every real legacy body, so the
# stamping assertions below can't pass vacuously.
DICT_STYLED = (
    '<div class="yomitan-glossary">'
    '<style>.yomitan-glossary [data-dictionary="X"]{color:red}</style>'
    '<ol data-count="1"><li data-dictionary="X">x</li></ol></div>'
)
# A genuine Yomitan export: yomitan-glossary wrapper but NO data-count.
YOMITAN_EXPORT = '<div class="yomitan-glossary"><ol><li>x</li></ol></div>'
# A v2.7.8+ single-carrier card: LEADING base sheet (ol[data-count] selector).
LEGACY_LEADING = "<style>.yomitan-glossary ol[data-count]{margin:0}</style>" + BARE


def _cfg(**over):
    base = create_default_config()
    fields = {**base.anki_fields, "glossary": "Glossary"}
    return replace(base, anki_note_type="Lapis", anki_fields=fields, **over)


def _note(note_id, glossary_value, *, field="Glossary"):
    return {"noteId": note_id, "fields": {field: {"value": glossary_value}}}


def _svc(notes):
    svc = MagicMock()
    svc.find_notes.return_value = [n["noteId"] for n in notes]
    svc.notes_info.return_value = notes
    svc.update_notes_fields.side_effect = lambda updates: len(updates)
    return svc


@pytest.fixture(autouse=True)
def _no_disk_io(monkeypatch):
    """Avoid real dictionary-registry / SQLite I/O; the base sheet stays real."""
    monkeypatch.setattr(card_restyler, "collect_dictionary_css_entries", lambda config: [])


class TestRestyleMinedCards:
    def test_bare_card_gets_trailing_block(self):
        svc = _svc([_note(1, BARE)])
        result = restyle_mined_cards(svc, _cfg())
        assert result.restyled == 1
        (updates,), _ = svc.update_notes_fields.call_args
        nid, fields = updates[0]
        assert nid == 1
        # Trailing, never leading: original body leads verbatim.
        assert fields["Glossary"] == f"{BARE}<style>{_variant_for(BARE)}</style>"

    def test_legacy_per_dict_style_is_restyled(self):
        # Blocker fix: a per-dict <style> lacks the base sheet, so it must be restyled,
        # NOT skipped as "already styled".
        svc = _svc([_note(1, DICT_STYLED)])
        result = restyle_mined_cards(svc, _cfg())
        assert result.restyled == 1
        assert result.skipped_styled == 0

    def test_yomitan_export_not_touched(self):
        # Safety: no data-count → foreign card, left alone.
        svc = _svc([_note(1, YOMITAN_EXPORT)])
        result = restyle_mined_cards(svc, _cfg())
        assert result.restyled == 0
        assert result.skipped_no_markup == 1
        svc.update_notes_fields.assert_not_called()

    def test_leading_block_migrates_to_tail(self):
        # THE Kiku migration: a v2.7.8+ leading base head moves to the field
        # tail (a leading <style> is head-hoisted by DOMParser and lost), and
        # the stale base swaps to this field's tree-shaken variant. BARE's
        # envelope stays unstamped (no carried CSS), so its variant is
        # core+unstyled-chrome — NOT the full sheet (Issue #93 shrink).
        svc = _svc([_note(1, LEGACY_LEADING)])
        result = restyle_mined_cards(svc, _cfg())
        assert result.restyled == 1
        assert result.skipped_styled == 0
        (updates,), _ = svc.update_notes_fields.call_args
        assert updates[0][1]["Glossary"] == f"{BARE}<style>{_variant_for(BARE)}</style>"
        assert _variant_for(BARE) == base_css_variant(frozenset({"unstyled-chrome"}))

    def test_current_format_card_skipped(self):
        # A card already carrying the CURRENT per-field trailing block is an
        # idempotent no-op → skipped. Baked through the same attach seam
        # mining uses, so mine-bytes == restyle-bytes is pinned here.
        current = attach_card_style_block(BARE, dict_css_entries=[])
        svc = _svc([_note(1, current)])
        result = restyle_mined_cards(svc, _cfg())
        assert result.restyled == 0
        assert result.skipped_styled == 1
        svc.update_notes_fields.assert_not_called()

    def test_migration_preserves_dict_css_tail_and_stamps(self):
        # A stale LEADING base with a per-dict dict_css tail: block moves to the
        # tail, base swaps, tail preserved verbatim, and the card's own carried
        # CSS stamps its envelope. Stamping must precede the variant pick: once
        # X is stamped the card has no unstamped envelope, so the head shrinks
        # to the CORE-ONLY variant.
        tail = '.yomitan-glossary [data-dictionary="X"]{color:red}'
        stale = f"<style>STALE ol[data-count]{{}}\n{tail}</style>{BARE}"
        svc = _svc([_note(1, stale)])
        result = restyle_mined_cards(svc, _cfg())
        assert result.restyled == 1
        (updates,), _ = svc.update_notes_fields.call_args
        written = updates[0][1]["Glossary"]
        assert written == f"{BARE_STAMPED}<style>{base_css_variant(frozenset())}\n{tail}</style>"

    def test_migration_rerun_converges_on_carried_css_stamping_shape(self):
        # THE idempotency shape that breaks if witnesses are computed pre-stamp:
        # run 1 stamps the envelope (carried CSS) AND picks the variant from the
        # stamped body; run 2 must then be a pure no-op — a pre-stamp witness
        # would see run 1's output stamped, pick a smaller variant, and rewrite
        # forever.
        tail = '.yomitan-glossary [data-dictionary="X"]{color:red}'
        stale = f"<style>STALE ol[data-count]{{}}\n{tail}</style>{BARE}"
        svc = _svc([_note(1, stale)])
        restyle_mined_cards(svc, _cfg())
        (updates,), _ = svc.update_notes_fields.call_args
        written = updates[0][1]["Glossary"]
        svc2 = _svc([_note(1, written)])
        result2 = restyle_mined_cards(svc2, _cfg())
        assert result2.restyled == 0
        assert result2.skipped_styled == 1
        svc2.update_notes_fields.assert_not_called()

    def test_fresh_attach_then_rerun_converges(self):
        # A legacy bare card gets the per-field slim block appended; feeding the
        # written value back through the service is a no-op.
        svc = _svc([_note(1, BARE)])
        restyle_mined_cards(svc, _cfg())
        (updates,), _ = svc.update_notes_fields.call_args
        written = updates[0][1]["Glossary"]
        svc2 = _svc([_note(1, written)])
        result2 = restyle_mined_cards(svc2, _cfg())
        assert result2.restyled == 0
        assert result2.skipped_styled == 1

    def test_witnesses_are_field_only(self):
        # Isolation pin (replaces the old cross-field witness contract): a
        # witness-bearing element present only in the note's OTHER mapped field
        # must be ABSENT from this field's variant. Cross-field witnessing
        # would diverge from mining's per-field blocks and rewrite fresh
        # both-mapped cards forever.
        cfg = _cfg()
        def_field = cfg.anki_fields["definition"]
        note = {
            "noteId": 1,
            "fields": {
                "Glossary": {"value": LEGACY_LEADING},
                def_field: {"value": '<img class="gloss-image" src="x.svg">'},
            },
        }
        svc = _svc([note])
        result = restyle_mined_cards(svc, cfg)
        assert result.restyled == 1
        (updates,), _ = svc.update_notes_fields.call_args
        written = updates[0][1]["Glossary"]
        assert written == f"{BARE}<style>{base_css_variant(frozenset({'unstyled-chrome'}))}</style>"
        assert "gloss-image" not in written

    def test_both_mapped_fields_migrated_in_one_write(self):
        # Single-carrier-era both-mapped card: glossary carried the (leading)
        # block, definition shipped naked. One restyle pass migrates the
        # glossary block to its tail AND fresh-attaches a block to the
        # definition — batched as ONE update entry for the note.
        cfg = _cfg()
        def_field = cfg.anki_fields["definition"]
        note = {
            "noteId": 1,
            "fields": {
                "Glossary": {"value": LEGACY_LEADING},
                def_field: {"value": BARE},
            },
        }
        svc = _svc([note])
        result = restyle_mined_cards(svc, cfg)
        assert result == RestyleResult(1, 1, 0, 0)
        (updates,), _ = svc.update_notes_fields.call_args
        assert len(updates) == 1
        nid, fields = updates[0]
        assert nid == 1
        expected = f"{BARE}<style>{_variant_for(BARE)}</style>"
        assert fields["Glossary"] == expected
        assert fields[def_field] == expected

    def test_mine_then_restyle_is_noop_with_asymmetric_witnesses(self):
        # Cross-writer idempotency with ASYMMETRIC per-field witnesses (the
        # glossary carries a gloss-image, the definition does not) — symmetric
        # fixtures would pass even under a cross-field-witness regression,
        # leaving the field-only invariant unpinned.
        cfg = _cfg()
        def_field = cfg.anki_fields["definition"]
        gloss_body = (
            '<div class="yomitan-glossary"><ol data-count="1">'
            '<li data-dictionary="X"><img class="gloss-image" src="p.svg">x</li></ol></div>'
        )
        note = {
            "noteId": 1,
            "fields": {
                "Glossary": {"value": attach_card_style_block(gloss_body, dict_css_entries=[])},
                def_field: {"value": attach_card_style_block(BARE, dict_css_entries=[])},
            },
        }
        svc = _svc([note])
        result = restyle_mined_cards(svc, cfg)
        assert result == RestyleResult(1, 0, 1, 0)
        svc.update_notes_fields.assert_not_called()

    def test_dict_styled_fresh_attach_stamps_and_converges(self, monkeypatch):
        # Round-2 judge HIGH: the v2.7.0–2.7.7 DICT_STYLED cohort goes through
        # fresh-attach — the envelope MUST be stamped (else the base sheet's
        # gap-fillers out-specify the dict's own scoped CSS, re-opening #87)
        # and a rerun must be a no-op. Current config also ships CSS for X, so
        # the appended block carries it (filtered to this field's dicts).
        scoped = '.yomitan-glossary [data-dictionary="X"] li{color:red}'
        monkeypatch.setattr(card_restyler, "collect_dictionary_css_entries", lambda config: [("X", scoped)])
        svc = _svc([_note(1, DICT_STYLED)])
        result = restyle_mined_cards(svc, _cfg())
        assert result.restyled == 1
        (updates,), _ = svc.update_notes_fields.call_args
        written = updates[0][1]["Glossary"]
        assert '<li data-dictionary="X" data-has-styles="">' in written
        assert written.endswith("</style>")
        assert scoped in written
        # Rerun: pure no-op.
        svc2 = _svc([_note(1, written)])
        result2 = restyle_mined_cards(svc2, _cfg())
        assert result2 == RestyleResult(1, 0, 1, 0)

    def test_service_is_idempotent(self):
        # Feed a migrated card back through the service (same config) → no-op.
        svc = _svc([_note(1, LEGACY_LEADING)])
        restyle_mined_cards(svc, _cfg())
        (updates,), _ = svc.update_notes_fields.call_args
        refreshed_value = updates[0][1]["Glossary"]
        svc2 = _svc([_note(1, refreshed_value)])
        result2 = restyle_mined_cards(svc2, _cfg())
        assert result2.restyled == 0
        assert result2.skipped_styled == 1
        svc2.update_notes_fields.assert_not_called()

    def test_two_style_body_replaces_exactly_ours(self):
        # A pre-v2.7.8-migrated card: its own per-dict <style> (inside the
        # envelope) plus our base block. Exactly OUR block (the one whose inner
        # CSS holds the ol[data-count] token) is replaced/moved; the per-dict
        # block survives verbatim in the body.
        v = (
            "<style>STALE ol[data-count]{}</style>"
            '<div class="yomitan-glossary"><style>.legacy{}</style>'
            '<ol data-count="1"><li data-dictionary="X">x</li></ol></div>'
        )
        svc = _svc([_note(1, v)])
        result = restyle_mined_cards(svc, _cfg())
        assert result.restyled == 1
        (updates,), _ = svc.update_notes_fields.call_args
        written = updates[0][1]["Glossary"]
        assert "<style>.legacy{}</style>" in written  # body's own <style> survives verbatim
        assert written.endswith("</style>")
        assert not written.startswith("<style>")
        assert "STALE" not in written  # the stale base is gone, replaced in the tail block
        assert written.count("<style>") == 2  # per-dict block + exactly one base block

    def test_unclosed_our_style_left_untouched(self):
        # Ownership token present but no matching </style> — malformed,
        # leave untouched (skipped, never corrupts).
        v = BARE + "<style>ol[data-count]{}"
        svc = _svc([_note(1, v)])
        result = restyle_mined_cards(svc, _cfg())
        assert result.restyled == 0
        assert result.skipped_styled == 1
        svc.update_notes_fields.assert_not_called()

    def test_two_base_blocks_left_untouched(self):
        # Two blocks both carrying the ownership token — hand-edited beyond
        # repair; refuse rather than guess.
        v = "<style>A ol[data-count]{}</style><style>B ol[data-count]{}</style>" + BARE
        svc = _svc([_note(1, v)])
        result = restyle_mined_cards(svc, _cfg())
        assert result.restyled == 0
        assert result.skipped_styled == 1
        svc.update_notes_fields.assert_not_called()

    def test_counts_exact_tuple(self):
        # A 5-note mix exercises every branch; migrated + fresh-attached fold
        # into restyled.
        current = attach_card_style_block(BARE, dict_css_entries=[])
        notes = [
            _note(1, LEGACY_LEADING),  # leading base → migrated
            _note(2, BARE),  # no base → fresh-attached
            _note(3, current),  # current format → skipped_styled (idempotent)
            _note(4, YOMITAN_EXPORT),  # no data-count → skipped_no_markup
            _note(5, "<div>plain</div>"),  # not a miner card → skipped_no_markup
        ]
        svc = _svc(notes)
        result = restyle_mined_cards(svc, _cfg())
        assert result == RestyleResult(5, 2, 1, 2)

    def test_mixed_note_counts_restyled_once(self):
        # Per-note aggregation: one field changes, the other is already
        # current → the note counts as restyled exactly once and the counters
        # still sum to scanned.
        cfg = _cfg()
        def_field = cfg.anki_fields["definition"]
        note = {
            "noteId": 1,
            "fields": {
                "Glossary": {"value": attach_card_style_block(BARE, dict_css_entries=[])},  # current
                def_field: {"value": BARE},  # needs fresh attach
            },
        }
        svc = _svc([note])
        result = restyle_mined_cards(svc, cfg)
        assert result == RestyleResult(1, 1, 0, 0)
        (updates,), _ = svc.update_notes_fields.call_args
        assert list(updates[0][1].keys()) == [def_field]

    def test_no_styling_field_mapped_is_noop(self):
        # Noop only when NEITHER glossary NOR definition is mapped — with no
        # carrier field there is nowhere to attach a block.
        cfg = replace(_cfg(), anki_fields={**_cfg().anki_fields, "glossary": "", "definition": ""})
        svc = MagicMock()
        result = restyle_mined_cards(svc, cfg)
        assert result == RestyleResult(0, 0, 0, 0)
        svc.find_notes.assert_not_called()

    def test_definition_field_processed_when_glossary_unmapped(self):
        # Parity with fresh mining: default config maps definition but not
        # glossary, so the DEFINITION field carries its own trailing block.
        cfg = replace(_cfg(), anki_fields={**_cfg().anki_fields, "glossary": ""})
        def_field = cfg.anki_fields["definition"]
        svc = _svc([_note(1, BARE, field=def_field)])
        result = restyle_mined_cards(svc, cfg)
        assert result.restyled == 1
        (updates,), _ = svc.update_notes_fields.call_args
        nid, fields = updates[0]
        assert nid == 1
        assert fields[def_field] == f"{BARE}<style>{_variant_for(BARE)}</style>"

    def test_no_writer_emits_leading_style(self):
        # Regression pin for the head-hoist hazard: whatever cohort goes in,
        # the written field NEVER starts with <style> (a leading block is
        # hoisted into <head> by DOMParser and dropped from body.innerHTML —
        # the Kiku bug).
        tail = '.yomitan-glossary [data-dictionary="X"]{color:red}'
        notes = [
            _note(1, BARE),
            _note(2, DICT_STYLED),
            _note(3, LEGACY_LEADING),
            _note(4, f"<style>STALE ol[data-count]{{}}\n{tail}</style>{BARE}"),
        ]
        svc = _svc(notes)
        restyle_mined_cards(svc, _cfg())
        (updates,), _ = svc.update_notes_fields.call_args
        for _nid, fields in updates:
            for value in fields.values():
                assert not value.startswith("<style>")
                assert value.endswith("</style>")

    def test_deleted_and_missing_field_skipped(self):
        notes = [{}, {"noteId": 2, "fields": {"Other": {"value": "x"}}}, _note(3, BARE)]
        svc = MagicMock()
        svc.find_notes.return_value = [1, 2, 3]
        svc.notes_info.return_value = notes
        svc.update_notes_fields.side_effect = lambda updates: len(updates)
        result = restyle_mined_cards(svc, _cfg())
        assert result.restyled == 1  # only note 3 was a bare miner card

    def test_cancel_stops_after_first_chunk(self, monkeypatch):
        monkeypatch.setattr(card_restyler, "_CHUNK", 1)  # one note per chunk
        svc = MagicMock()
        svc.find_notes.return_value = [1, 2]
        svc.notes_info.side_effect = lambda ids: [_note(ids[0], BARE)]
        svc.update_notes_fields.side_effect = lambda updates: len(updates)
        state = {"n": 0}

        def is_cancelled():
            state["n"] += 1
            return state["n"] > 1  # allow the first chunk, cancel before the second

        result = restyle_mined_cards(svc, _cfg(), is_cancelled=is_cancelled)
        assert svc.notes_info.call_count == 1  # second chunk never read
        assert result.restyled == 1  # first chunk's write is committed

    def test_css_entries_collected_once(self, monkeypatch):
        collect = MagicMock(return_value=[])
        monkeypatch.setattr(card_restyler, "collect_dictionary_css_entries", collect)
        svc = _svc([_note(1, BARE), _note(2, BARE)])
        restyle_mined_cards(svc, _cfg())
        collect.assert_called_once()

    def test_find_notes_query_is_escaped(self):
        svc = _svc([])
        restyle_mined_cards(svc, replace(_cfg(), anki_note_type='Core_2k "x"'))
        (query,), _ = svc.find_notes.call_args
        assert query == 'note:"Core\\_2k \\"x\\""'


class TestRestyleField:
    """Pure helper: migrate/refresh OUR block to the field tail, preserve the
    dict_css tail + body verbatim; fresh-attach (stamp + append) otherwise.
    Returns None only for malformed <style> structure."""

    @staticmethod
    def _restyle(value, entries=(), current_dict_css=""):
        return card_restyler._restyle_field(value, list(entries), current_dict_css)

    def test_unclosed_our_style_returns_none(self):
        assert self._restyle(BARE + "<style>ol[data-count]{}") is None

    def test_two_base_blocks_returns_none(self):
        v = "<style>A ol[data-count]{}</style><style>B ol[data-count]{}</style>" + BARE
        assert self._restyle(v) is None

    def test_leading_migrates_to_tail_preserving_multiline_dict_tail(self):
        tail = ".dictA{}\n.dictB{}"
        v = f"<style>OLD ol[data-count]{{}}\n{tail}</style><div>body</div>"
        out = self._restyle(v)
        # Body preserved byte-for-byte (no envelope → nothing to stamp), block
        # at the tail with a freshly computed base + the verbatim dict tail.
        assert out == f"<div>body</div><style>{base_css_variant(frozenset())}\n{tail}</style>"

    def test_current_trailing_format_is_fixpoint(self):
        v = attach_card_style_block(BARE, dict_css_entries=[])
        assert self._restyle(v) == v

    def test_stamps_from_carried_tail_not_config(self):
        # Head tail carries scoped CSS for X; the body's unstamped envelope
        # stamps from the card's OWN carried CSS — entries/current config are
        # NOT consulted on the extract path.
        tail = '.yomitan-glossary [data-dictionary="X"]{color:red}'
        v = f"<style>OLD ol[data-count]{{}}\n{tail}</style>{BARE}"
        out = self._restyle(v, entries=[("Y", ".y{}")], current_dict_css=".y{}")
        assert out == f"{BARE_STAMPED}<style>{base_css_variant(frozenset())}\n{tail}</style>"
        assert ".y{}" not in out  # carried CSS only, never re-collected

    def test_fresh_attach_filters_current_config_to_field(self):
        entries = [("X", ".x{color:red}"), ("Y", ".y{color:blue}")]
        out = self._restyle(BARE, entries=entries, current_dict_css=".x{color:red}\n\n.y{color:blue}")
        assert out is not None
        assert ".x{color:red}" in out
        assert ".y{color:blue}" not in out  # Y has no envelope in this field
        assert out.startswith(BARE_STAMPED) or out.startswith(BARE)

    def test_fresh_attach_stamps_via_current_config_gate(self):
        # The legacy-prepend gate: current config ships CSS for X → X's
        # envelope is stamped even though the body carries no CSS of its own.
        scoped = '.yomitan-glossary [data-dictionary="X"] li{color:red}'
        out = self._restyle(BARE, entries=[("X", scoped)], current_dict_css=scoped)
        assert out is not None
        assert '<li data-dictionary="X" data-has-styles="">' in out


class TestLegacyEnvelopeStamping:
    """Legacy envelopes governed by scoped dictionary CSS get data-has-styles.

    Without the stamp, the appended base sheet's gated gap-fillers apply and
    out-specify the scoped dict CSS — reproducing the tiny+grey bug on the
    restyle path. Assertions are always envelope-scoped, value-bearing
    (``data-has-styles=""``): the base sheet itself contains the bare token
    inside every ``:not([data-has-styles])`` gate, so whole-field token checks
    would be vacuous.
    """

    def _written(self, svc):
        (updates,), _ = svc.update_notes_fields.call_args
        return updates[0][1]["Glossary"]

    def test_dict_styled_envelope_stamped_from_own_style_block(self):
        svc = _svc([_note(1, DICT_STYLED)])
        assert 'data-has-styles=""' not in DICT_STYLED
        result = restyle_mined_cards(svc, _cfg())
        assert result.restyled == 1
        assert '<li data-dictionary="X" data-has-styles="">' in self._written(svc)

    def test_config_side_scoped_css_stamps_envelope(self, monkeypatch):
        # Envelope with NO scoped block in its own body, but the current config
        # contributes scoped CSS for that title into the appended block — the
        # restyler injects that CSS, so the envelope must be gated too.
        scoped = '.yomitan-glossary [data-dictionary="X"] li {color: red}'
        monkeypatch.setattr(card_restyler, "collect_dictionary_css_entries", lambda config: [("X", scoped)])
        svc = _svc([_note(1, BARE)])
        result = restyle_mined_cards(svc, _cfg())
        assert result.restyled == 1
        assert '<li data-dictionary="X" data-has-styles="">' in self._written(svc)

    def test_unstyled_envelope_stays_unstamped(self):
        svc = _svc([_note(1, BARE)])
        result = restyle_mined_cards(svc, _cfg())
        assert result.restyled == 1
        # Assert on the <li> itself — the base sheet carries the bare token.
        assert '<li data-dictionary="X">x' in self._written(svc)

    def test_multi_envelope_body_stamps_exactly_the_styled_one(self):
        body = (
            '<div class="yomitan-glossary">'
            '<style>.yomitan-glossary [data-dictionary="X"]{color:red}</style>'
            '<ol data-count="1"><li data-dictionary="X">x</li></ol></div>'
            '<div class="yomitan-glossary">'
            '<ol data-count="1"><li data-dictionary="Y">y</li></ol></div>'
        )
        svc = _svc([_note(1, body)])
        result = restyle_mined_cards(svc, _cfg())
        assert result.restyled == 1
        written = self._written(svc)
        assert '<li data-dictionary="X" data-has-styles="">' in written
        assert '<li data-dictionary="Y">y' in written

    def test_escaped_titles_round_trip_through_membership_key(self):
        # The crux of the restyler key: html.unescape(attr) -> css_string_escape
        # must reproduce the scoped-CSS selector for names needing HTML escaping.
        # Envelope built by the real renderer (_scoped_css="" -> faithful
        # html.escape'd attribute, NO pre-stamp); scoped CSS built by the real
        # scope_dict_css from the same display name.
        import html

        from anki_miner.services.dictionary.dict_css_scope import scope_dict_css

        for name in ("A&B Dictionary", 'The "Big" Dictionary'):
            attr = html.escape(name, quote=True)
            scoped = scope_dict_css("li { color: red }", name)
            value = (
                f'<div class="yomitan-glossary"><style>{scoped}</style>'
                f'<ol data-count="1"><li data-dictionary="{attr}">x</li></ol></div>'
            )
            assert f'<li data-dictionary="{attr}" data-has-styles="">' not in value
            svc = _svc([_note(1, value)])
            result = restyle_mined_cards(svc, _cfg())
            assert result.restyled == 1
            assert f'<li data-dictionary="{attr}" data-has-styles="">' in self._written(svc)

    def test_stamping_is_idempotent(self):
        # An already-stamped envelope doesn't match the legacy pattern (extra
        # attribute before ">"), so re-stamping is a no-op.
        stamped = '<li data-dictionary="X" data-has-styles="">x</li>'
        out = card_restyler._stamp_styled_envelopes(stamped, '.yomitan-glossary [data-dictionary="X"] li {color: red}')
        assert out == stamped
