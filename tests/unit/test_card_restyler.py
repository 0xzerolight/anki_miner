"""Tests for the Restyle Mined Cards service (card_restyler).

Format under test: every mapped miner field carries its own TRAILING
``<style>`` block (per-field self-containment; a leading block is head-hoisted
by DOMParser on JS note types and lost). The restyler refreshes that block on
current-markup fields, migrates a v2.7.8+ single-carrier LEADING block to the
tail, and leaves legacy-markup fields (tag chips, ``<ul class="gloss-list">``)
untouched.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from anki_miner.config import create_default_config
from anki_miner.services import card_restyler
from anki_miner.services.card_restyler import RestyleResult, is_legacy_markup, restyle_mined_cards
from anki_miner.services.dictionary.card_style_block import (
    OWNED_STYLE_MARKER,
    attach_card_style_block,
    base_css,
)
from anki_miner.services.dictionary.dict_css_scope import scope_dict_css


def _owned(css: str) -> str:
    return OWNED_STYLE_MARKER + "}" + css


# A miner card mined bare (markup, no <style>).
BARE = '<div class="yomitan-glossary"><ol data-count="1"><li data-dictionary="X">x</li></ol></div>'
# A v2.7.0–v2.7.7 dict-styled card: per-dict <style> (scoped, no base sheet).
DICT_STYLED = (
    '<div class="yomitan-glossary">'
    '<style>.yomitan-glossary [data-dictionary="X"]{color:red}</style>'
    '<ol data-count="1"><li data-dictionary="X">x</li></ol></div>'
)
# A genuine Yomitan export: yomitan-glossary wrapper but NO data-count.
YOMITAN_EXPORT = '<div class="yomitan-glossary"><ol><li>x</li></ol></div>'
# A v2.7.8+ single-carrier card: LEADING base sheet (ol[data-count] selector).
LEGACY_LEADING = f"<style>{_owned('.yomitan-glossary ol[data-count]{margin:0}')}</style>" + BARE
# The clean glossary shape: numbered senses, a circle-bulleted gloss list.
CURRENT_BODY = (
    '<div class="yomitan-glossary"><ol data-count="1"><li data-dictionary="JMdict" data-dictionary-id="jmdict">'
    '<ol class="gloss-list"><li class="gloss-item"><ol class="gloss-glosses" lang="en"><li>dinner</li>'
    '<li>evening meal</li></ol></li><li class="gloss-item">supper</li></ol></li></ol></div>'
)
# A card mined before the clean shape: tag chips, a ul sense list, and the old
# stamped envelope + embedded sheet.
LEGACY_BODY = (
    '<div class="yomitan-glossary"><ol data-count="1">'
    '<li data-dictionary="JMdict [2026-06-28]" data-dictionary-id="jmdict" data-has-styles="">'
    '<i>(<span class="gloss-tag" data-category="partOfSpeech" title="noun (common)">n</span>, JMdict [2026-06-28])</i>'
    '<ul class="gloss-list" data-count="1"><li class="gloss-item"><div class="gloss-content">dinner</div></li></ul>'
    "</li></ol></div>"
)
LEGACY_CARD = (
    f"{LEGACY_BODY}<style>"
    f"{_owned('.yomitan-glossary ol[data-count] ul.gloss-list{display: block !important;}')}"
    '\n.yomitan-glossary [data-dictionary="JMdict [2026-06-28]"]{color:red}</style>'
)


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
    svc.update_notes_fields.side_effect = lambda updates: [note_id for note_id, _fields in updates]
    return svc


@pytest.fixture(autouse=True)
def _no_disk_io(monkeypatch):
    """Avoid real dictionary-registry / SQLite I/O; the base sheet stays real."""
    monkeypatch.setattr(card_restyler, "collect_dictionary_css_entries", lambda config: [])


class TestLegacyMarkup:
    @pytest.mark.parametrize(
        "body",
        [
            LEGACY_BODY,
            '<li data-dictionary="X"><span class="gloss-tag" title="noun">n</span>x</li>',
            '<li data-dictionary="X"><ul class="gloss-list" data-count="1"><li class="gloss-item">x</li></ul></li>',
        ],
        ids=["chips-and-ul", "chips-only", "ul-only"],
    )
    def test_detects_chips_or_the_ul_sense_list(self, body):
        assert is_legacy_markup(body)

    @pytest.mark.parametrize("body", [BARE, CURRENT_BODY, attach_card_style_block(CURRENT_BODY, dict_css_entries=[])])
    def test_current_shape_is_not_legacy(self, body):
        assert not is_legacy_markup(body)

    def test_legacy_markup_field_is_skipped_and_counted(self):
        # The current sheet styles neither chips nor the ul sense list, and the
        # restyler never rewrites bodies: swapping the old block would strip the
        # card's styling. Left byte-identical, counted as "left untouched".
        assert card_restyler._restyle_field(LEGACY_CARD, []) != LEGACY_CARD  # the skip is what keeps it
        svc = _svc([_note(1, LEGACY_CARD)])
        result = restyle_mined_cards(svc, _cfg(), progress=None, is_cancelled=lambda: False)
        assert (result.skipped_styled, result.restyled) == (1, 0)
        assert result == RestyleResult(1, 0, 1, 0)
        svc.update_notes_fields.assert_not_called()

    def test_legacy_field_left_alone_while_its_current_sibling_refreshes(self):
        cfg = _cfg()
        def_field = cfg.anki_fields["definition"]
        note = {
            "noteId": 1,
            "fields": {"Glossary": {"value": LEGACY_CARD}, def_field: {"value": CURRENT_BODY}},
        }
        svc = _svc([note])
        result = restyle_mined_cards(svc, cfg)
        assert result == RestyleResult(1, 1, 0, 0)
        (updates,), _ = svc.update_notes_fields.call_args
        assert updates == [(1, {def_field: f"{CURRENT_BODY}<style>{base_css(CURRENT_BODY)}</style>"})]

    def test_current_markup_field_gets_fresh_base(self):
        # New-shape body + stale block → the block is replaced by base_css(body)
        # plus the carried dict CSS, verbatim.
        tail = '.yomitan-glossary [data-dictionary="JMdict"]{color:red}'
        stale = f"{CURRENT_BODY}<style>{_owned('STALE ol[data-count]{}')}\n{tail}</style>"
        svc = _svc([_note(1, stale)])
        result = restyle_mined_cards(svc, _cfg())
        assert result == RestyleResult(1, 1, 0, 0)
        (updates,), _ = svc.update_notes_fields.call_args
        assert updates[0][1]["Glossary"] == f"{CURRENT_BODY}<style>{base_css(CURRENT_BODY)}\n{tail}</style>"


class TestRestyleMinedCards:
    def test_owned_style_marker_is_exact(self):
        third_party = "<style>.vendor ol[data-count]{color:red}\n.keep{display:block}</style>"
        value = third_party + BARE

        out = card_restyler._restyle_field(value, [])

        assert out is not None
        assert third_party in out

    def test_partial_update_counts_failed_and_preserves_counter_sum(self):
        svc = _svc([_note(1, BARE), _note(2, BARE)])
        svc.update_notes_fields.side_effect = None
        svc.update_notes_fields.return_value = [1]

        result = restyle_mined_cards(svc, _cfg())

        assert result.restyled == 1
        assert result.failed == 1
        assert result.restyled + result.failed + result.skipped_styled + result.skipped_no_markup == result.scanned

    def test_bare_card_gets_trailing_block(self):
        svc = _svc([_note(1, BARE)])
        result = restyle_mined_cards(svc, _cfg())
        assert result.restyled == 1
        (updates,), _ = svc.update_notes_fields.call_args
        nid, fields = updates[0]
        assert nid == 1
        # Trailing, never leading: original body leads verbatim.
        assert fields["Glossary"] == f"{BARE}<style>{base_css(BARE)}</style>"

    def test_legacy_per_dict_style_is_restyled(self):
        # A per-dict <style> lacks the base sheet, so it must be restyled, NOT
        # skipped as "already styled".
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
        # the stale base swaps to this field's base.
        svc = _svc([_note(1, LEGACY_LEADING)])
        result = restyle_mined_cards(svc, _cfg())
        assert result.restyled == 1
        assert result.skipped_styled == 0
        (updates,), _ = svc.update_notes_fields.call_args
        assert updates[0][1]["Glossary"] == f"{BARE}<style>{base_css(BARE)}</style>"
        assert base_css(BARE) == base_css("")

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

    def test_migration_preserves_dict_css_tail(self):
        # A stale LEADING base with a per-dict dict_css tail: block moves to the
        # tail, base swaps, tail and body preserved verbatim.
        tail = '.yomitan-glossary [data-dictionary="X"]{color:red}'
        stale = f"<style>{_owned('STALE ol[data-count]{}')}\n{tail}</style>{BARE}"
        svc = _svc([_note(1, stale)])
        result = restyle_mined_cards(svc, _cfg())
        assert result.restyled == 1
        (updates,), _ = svc.update_notes_fields.call_args
        written = updates[0][1]["Glossary"]
        assert written == f"{BARE}<style>{base_css(BARE)}\n{tail}</style>"

    def test_migration_rerun_converges(self):
        tail = '.yomitan-glossary [data-dictionary="X"]{color:red}'
        stale = f"<style>{_owned('STALE ol[data-count]{}')}\n{tail}</style>{BARE}"
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
        # A bare card gets the per-field block appended; feeding the written
        # value back through the service is a no-op.
        svc = _svc([_note(1, BARE)])
        restyle_mined_cards(svc, _cfg())
        (updates,), _ = svc.update_notes_fields.call_args
        written = updates[0][1]["Glossary"]
        svc2 = _svc([_note(1, written)])
        result2 = restyle_mined_cards(svc2, _cfg())
        assert result2.restyled == 0
        assert result2.skipped_styled == 1

    def test_base_is_field_only(self):
        # Isolation pin: an image present only in the note's OTHER mapped field
        # must not pull the images region into this field's block. Cross-field
        # selection would diverge from mining's per-field blocks and rewrite
        # fresh both-mapped cards forever.
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
        assert written == f"{BARE}<style>{base_css('')}</style>"
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
        expected = f"{BARE}<style>{base_css(BARE)}</style>"
        assert fields["Glossary"] == expected
        assert fields[def_field] == expected

    def test_mine_then_restyle_is_noop_with_asymmetric_fields(self):
        # Cross-writer idempotency with ASYMMETRIC fields (the glossary carries a
        # gloss-image, the definition does not) — symmetric fixtures would pass
        # even under a cross-field regression, leaving the field-only invariant
        # unpinned.
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

    def test_dict_styled_fresh_attach_converges(self, monkeypatch):
        # The v2.7.0–2.7.7 DICT_STYLED cohort goes through fresh-attach: the body
        # stays byte-identical (no envelope stamp), the appended block carries the
        # current-config CSS for X, and a rerun is a no-op.
        scoped = '.yomitan-glossary [data-dictionary="X"] li{color:red}'
        monkeypatch.setattr(card_restyler, "collect_dictionary_css_entries", lambda config: [("x-id", "X", scoped)])
        svc = _svc([_note(1, DICT_STYLED)])
        result = restyle_mined_cards(svc, _cfg())
        assert result.restyled == 1
        (updates,), _ = svc.update_notes_fields.call_args
        written = updates[0][1]["Glossary"]
        assert written == f"{DICT_STYLED}<style>{base_css(DICT_STYLED)}\n{scoped}</style>"
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
            f"<style>{_owned('STALE ol[data-count]{}')}</style>"
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
        v = BARE + f"<style>{_owned('ol[data-count]{}')}"
        svc = _svc([_note(1, v)])
        result = restyle_mined_cards(svc, _cfg())
        assert result.restyled == 0
        assert result.skipped_styled == 1
        svc.update_notes_fields.assert_not_called()

    def test_two_base_blocks_left_untouched(self):
        # Two blocks both carrying the ownership token — hand-edited beyond
        # repair; refuse rather than guess.
        v = f"<style>{_owned('A ol[data-count]{}')}</style><style>{_owned('B ol[data-count]{}')}</style>" + BARE
        svc = _svc([_note(1, v)])
        result = restyle_mined_cards(svc, _cfg())
        assert result.restyled == 0
        assert result.skipped_styled == 1
        svc.update_notes_fields.assert_not_called()

    def test_counts_exact_tuple(self):
        # A 6-note mix exercises every branch; migrated + fresh-attached fold
        # into restyled, legacy markup folds into skipped_styled.
        current = attach_card_style_block(BARE, dict_css_entries=[])
        notes = [
            _note(1, LEGACY_LEADING),  # leading base → migrated
            _note(2, BARE),  # no base → fresh-attached
            _note(3, current),  # current format → skipped_styled (idempotent)
            _note(4, YOMITAN_EXPORT),  # no data-count → skipped_no_markup
            _note(5, "<div>plain</div>"),  # not a miner card → skipped_no_markup
            _note(6, LEGACY_CARD),  # legacy markup → skipped_styled (left untouched)
        ]
        svc = _svc(notes)
        result = restyle_mined_cards(svc, _cfg())
        assert result == RestyleResult(6, 2, 2, 2)

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
        assert fields[def_field] == f"{BARE}<style>{base_css(BARE)}</style>"

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
            _note(4, f"<style>{_owned('STALE ol[data-count]{}')}\n{tail}</style>{BARE}"),
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
        svc.update_notes_fields.side_effect = lambda updates: [note_id for note_id, _fields in updates]
        result = restyle_mined_cards(svc, _cfg())
        assert result.restyled == 1  # only note 3 was a bare miner card

    def test_cancel_stops_after_first_chunk(self, monkeypatch):
        monkeypatch.setattr(card_restyler, "_CHUNK", 1)  # one note per chunk
        svc = MagicMock()
        svc.find_notes.return_value = [1, 2]
        svc.notes_info.side_effect = lambda ids: [_note(ids[0], BARE)]
        svc.update_notes_fields.side_effect = lambda updates: [note_id for note_id, _fields in updates]
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
    dict_css tail + body verbatim; fresh-attach otherwise. Returns None only
    for malformed <style> structure."""

    @staticmethod
    def _restyle(value, entries=()):
        return card_restyler._restyle_field(value, list(entries))

    @pytest.mark.parametrize(
        "entries",
        [(), (("x-id", "X", '.yomitan-glossary [data-dictionary="X"]{color:red}'),)],
        ids=["no-dict-css", "dict-css"],
    )
    def test_a_freshly_mined_rtl_field_is_already_current(self, entries):
        # Writer convergence (S21): the RTL rule rides the block tail, which the
        # restyler carries verbatim, so a restyle of a fresh RTL card is a no-op.
        from anki_miner.services.dictionary.card_style_block import RTL_GLOSSARY_CSS

        mined = attach_card_style_block(BARE, dict_css_entries=list(entries), direction="rtl")
        assert mined.endswith("\n" + RTL_GLOSSARY_CSS + "</style>")
        assert self._restyle(mined, entries) == mined

    def test_unclosed_our_style_returns_none(self):
        assert self._restyle(BARE + f"<style>{_owned('ol[data-count]{}')}") is None

    def test_two_base_blocks_returns_none(self):
        v = f"<style>{_owned('A ol[data-count]{}')}</style><style>{_owned('B ol[data-count]{}')}</style>" + BARE
        assert self._restyle(v) is None

    def test_leading_migrates_to_tail_preserving_multiline_dict_tail(self):
        tail = ".dictA{}\n.dictB{}"
        v = f"<style>{_owned('OLD ol[data-count]{}')}\n{tail}</style><div>body</div>"
        out = self._restyle(v)
        # Body preserved byte-for-byte, block at the tail with a freshly
        # computed base + the verbatim dict tail.
        assert out == f"<div>body</div><style>{base_css('')}\n{tail}</style>"

    def test_current_trailing_format_is_fixpoint(self):
        v = attach_card_style_block(BARE, dict_css_entries=[])
        assert self._restyle(v) == v

    def test_pre_owned_marker_block_is_still_recognised(self):
        # Blocks minted before the owned-style marker open on the old literal
        # `--am-muted` declaration; only card_restyler's legacy literal
        # recognises them: replaced, never stacked.
        old_head = ".yomitan-glossary{--am-muted: rgba(128,128,128,0.95);}.yomitan-glossary ol[data-count]{margin:0}"
        tail = '.yomitan-glossary [data-dictionary="X"]{color:red}'
        out = self._restyle(f"{BARE}<style>{old_head}\n{tail}</style>")
        assert out == f"{BARE}<style>{base_css(BARE)}\n{tail}</style>"
        assert out.count("<style>") == 1

    def test_carried_tail_wins_over_config(self):
        # The extract path keeps the card's OWN carried CSS; entries/current
        # config are NOT consulted.
        tail = '.yomitan-glossary [data-dictionary="X"]{color:red}'
        v = f"<style>{_owned('OLD ol[data-count]{}')}\n{tail}</style>{BARE}"
        out = self._restyle(v, entries=[("x-id", "X", ".x{}")])
        assert out == f"{BARE}<style>{base_css(BARE)}\n{tail}</style>"
        assert ".x{}" not in out  # carried CSS only, never re-collected

    def test_fresh_attach_filters_current_config_to_field(self):
        entries = [("x-id", "X", ".x{color:red}"), ("y-id", "Y", ".y{color:blue}")]
        out = self._restyle(BARE, entries=entries)
        assert out == f"{BARE}<style>{base_css(BARE)}\n.x{{color:red}}</style>"  # Y has no envelope here

    def test_fresh_attach_keeps_css_for_legacy_title_envelope(self):
        title = "Jitendex.org [2026-06-06]"
        value = BARE.replace('data-dictionary="X"', f'data-dictionary="{title}"')
        scoped = scope_dict_css("li {color:red}", "jitendex", title)

        out = self._restyle(value, entries=[("jitendex", title, scoped)])

        assert out is not None
        assert 'data-dictionary-id="' not in value
        assert f'[data-dictionary="{title}"]:not([data-dictionary-id]) li {{color:red}}' in out

    def test_body_is_never_rewritten(self):
        # No envelope stamping: whatever CSS governs a dictionary, the body bytes
        # come back verbatim ahead of the block.
        scoped = '.yomitan-glossary [data-dictionary="X"] li{color:red}'
        out = self._restyle(BARE, entries=[("x-id", "X", scoped)])
        assert out is not None
        assert out.startswith(BARE + "<style>")
        assert "data-has-styles" not in out


def test_rejected_updates_reach_main_window_as_non_success(qtbot, patch_heavy_init, test_config, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    patch_heavy_init(test_config)
    from anki_miner.gui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    start_restyle = MagicMock()
    window.background_tasks.start_restyle_cards = start_restyle
    show_issue = MagicMock()
    monkeypatch.setattr(window, "show_screen_issue", show_issue)
    information = MagicMock()
    monkeypatch.setattr(QMessageBox, "information", information)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        MagicMock(return_value=QMessageBox.StandardButton.Yes),
    )

    window.restyle_mined_cards()
    on_result = start_restyle.call_args.args[3]
    on_result(RestyleResult(scanned=2, restyled=1, skipped_styled=0, skipped_no_markup=0, failed=1))

    assert window.status_bar.operation_label.property("level") == "error"
    information.assert_not_called()
    issue = show_issue.call_args.args[0]
    assert "1" in issue.summary
    assert "1" in issue.details
