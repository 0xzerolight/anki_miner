"""Tests for the one-time Restyle Mined Cards service (card_restyler)."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from anki_miner.config import create_default_config
from anki_miner.services import card_restyler
from anki_miner.services.card_restyler import RestyleResult, restyle_mined_cards
from anki_miner.services.dictionary.card_style_block import (
    base_css_variant,
    build_card_style_block,
    css_witnesses,
)


def _variant_for(html: str) -> str:
    """The tree-shaken base head the restyler must embed for this (stamped) HTML."""
    return base_css_variant(css_witnesses([html]))


# A miner card mined bare (markup, no <style>).
BARE = '<div class="yomitan-glossary"><ol data-count="1"><li data-dictionary="X">x</li></ol></div>'
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
# Already restyled (or a v2.7.8 card): base sheet present (ol[data-count] selector).
ALREADY = "<style>.yomitan-glossary ol[data-count]{margin:0}</style>" + BARE


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
    monkeypatch.setattr(card_restyler, "collect_dictionary_css", lambda config: "")


class TestRestyleMinedCards:
    def test_bare_card_restyled(self):
        svc = _svc([_note(1, BARE)])
        result = restyle_mined_cards(svc, _cfg())
        assert result.restyled == 1
        (updates,), _ = svc.update_notes_fields.call_args
        nid, fields = updates[0]
        assert nid == 1
        assert fields["Glossary"].startswith("<style>")
        assert fields["Glossary"].endswith(BARE)  # prepend, original preserved

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

    def test_stale_base_refreshed(self):
        # A card carrying a STALE base head is refreshed in place (not skipped):
        # the base swaps to this card's tree-shaken variant; body preserved.
        # BARE's envelope stays unstamped (no carried CSS), so its variant is
        # core+unstyled-chrome — NOT the full sheet (Issue #93 shrink).
        svc = _svc([_note(1, ALREADY)])  # ALREADY carries a stub `ol[data-count]{margin:0}` base
        result = restyle_mined_cards(svc, _cfg())
        assert result.restyled == 1
        assert result.skipped_styled == 0
        (updates,), _ = svc.update_notes_fields.call_args
        assert updates[0][1]["Glossary"] == f"<style>{_variant_for(BARE)}</style>{BARE}"
        assert _variant_for(BARE) == base_css_variant(frozenset({"unstyled-chrome"}))

    def test_current_base_card_skipped(self):
        # A card already carrying the CURRENT base is an idempotent no-op →
        # skipped. The baked block must use the card's own body as card_html:
        # the refresh recomputes the variant from that body, so a core-only
        # bake would read as stale and flip the skip to a rewrite.
        current = build_card_style_block(dict_css="", card_html=BARE) + BARE
        svc = _svc([_note(1, current)])
        result = restyle_mined_cards(svc, _cfg())
        assert result.restyled == 0
        assert result.skipped_styled == 1
        svc.update_notes_fields.assert_not_called()

    def test_refresh_preserves_dict_css_tail_and_stamps(self):
        # A stale base with a per-dict dict_css tail: base swaps, tail preserved
        # verbatim, and the card's own carried CSS stamps its envelope. The
        # stamping must precede the variant pick: once X is stamped the card has
        # no unstamped envelope, so the head shrinks to the CORE-ONLY variant.
        tail = '.yomitan-glossary [data-dictionary="X"]{color:red}'
        stale = f"<style>STALE ol[data-count]{{}}\n{tail}</style>{BARE}"
        svc = _svc([_note(1, stale)])
        result = restyle_mined_cards(svc, _cfg())
        assert result.restyled == 1
        (updates,), _ = svc.update_notes_fields.call_args
        written = updates[0][1]["Glossary"]
        assert written.startswith(f"<style>{base_css_variant(frozenset())}\n{tail}</style>")
        assert '<li data-dictionary="X" data-has-styles="">' in written

    def test_refresh_rerun_converges_on_carried_css_stamping_shape(self):
        # THE idempotency shape that breaks if witnesses are computed pre-stamp:
        # run 1 stamps the envelope (head-carried CSS) AND picks the variant
        # from the stamped body; run 2 must then be a pure no-op — a pre-stamp
        # witness would see run 1's output stamped, pick a smaller variant, and
        # rewrite forever.
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

    def test_prepend_then_rerun_converges(self):
        # A legacy bare card gets the per-card slim block prepended; feeding the
        # written value back through the service is a no-op (prepend → refresh
        # convergence on a slim-witness body).
        svc = _svc([_note(1, BARE)])
        restyle_mined_cards(svc, _cfg())
        (updates,), _ = svc.update_notes_fields.call_args
        written = updates[0][1]["Glossary"]
        assert written == f"<style>{_variant_for(BARE)}</style>{BARE}"
        svc2 = _svc([_note(1, written)])
        result2 = restyle_mined_cards(svc2, _cfg())
        assert result2.restyled == 0
        assert result2.skipped_styled == 1

    def test_witnesses_gathered_from_both_miner_fields(self):
        # The <style> is card-wide: an image in the note's OTHER miner field
        # (definition) must pull the images group into the glossary-carried head.
        cfg = _cfg()
        def_field = cfg.anki_fields["definition"]
        note = {
            "noteId": 1,
            "fields": {
                "Glossary": {"value": ALREADY},
                def_field: {"value": '<img class="gloss-image" src="x.svg">'},
            },
        }
        svc = _svc([note])
        result = restyle_mined_cards(svc, cfg)
        assert result.restyled == 1
        (updates,), _ = svc.update_notes_fields.call_args
        written = updates[0][1]["Glossary"]
        expected = base_css_variant(frozenset({"unstyled-chrome", "images"}))
        assert written == f"<style>{expected}</style>{BARE}"

    def test_refresh_service_is_idempotent(self):
        # Feed a refreshed card back through the service (same config) → no-op.
        svc = _svc([_note(1, ALREADY)])
        restyle_mined_cards(svc, _cfg())
        (updates,), _ = svc.update_notes_fields.call_args
        refreshed_value = updates[0][1]["Glossary"]
        svc2 = _svc([_note(1, refreshed_value)])
        result2 = restyle_mined_cards(svc2, _cfg())
        assert result2.restyled == 0
        assert result2.skipped_styled == 1
        svc2.update_notes_fields.assert_not_called()

    def test_non_conforming_head_token_in_later_block_skipped(self):
        # `ol[data-count]` present but only in a LATER <style>; the first block is a
        # per-dict style → refresh leaves it untouched (skipped, never corrupts).
        v = (
            '<style>.yomitan-glossary [data-dictionary="X"]{color:red}</style>'
            "<style>.yomitan-glossary ol[data-count]{margin:0}</style>"
            '<div class="yomitan-glossary"><ol data-count="1"><li data-dictionary="X">x</li></ol></div>'
        )
        svc = _svc([_note(1, v)])
        result = restyle_mined_cards(svc, _cfg())
        assert result.restyled == 0
        assert result.skipped_styled == 1
        svc.update_notes_fields.assert_not_called()

    def test_refresh_counts_exact_tuple(self):
        # A 5-note mix exercises every branch; refreshed + prepended fold into
        # restyled. `current` bakes with card_html=BARE (the idempotency rule:
        # a baked no-op fixture must use the matching card body).
        current = build_card_style_block(dict_css="", card_html=BARE) + BARE
        notes = [
            _note(1, ALREADY),  # stale base → refreshed
            _note(2, BARE),  # no base → prepended
            _note(3, current),  # current base → skipped_styled (idempotent)
            _note(4, YOMITAN_EXPORT),  # no data-count → skipped_no_markup
            _note(5, "<div>plain</div>"),  # not a miner card → skipped_no_markup
        ]
        svc = _svc(notes)
        result = restyle_mined_cards(svc, _cfg())
        assert result == RestyleResult(5, 2, 1, 2)

    def test_no_styling_field_mapped_is_noop(self):
        # Noop only when NEITHER glossary NOR definition is mapped — with no
        # carrier field there is nowhere to attach the card-wide block.
        cfg = replace(_cfg(), anki_fields={**_cfg().anki_fields, "glossary": "", "definition": ""})
        svc = MagicMock()
        result = restyle_mined_cards(svc, cfg)
        assert result == RestyleResult(0, 0, 0, 0)
        svc.find_notes.assert_not_called()

    def test_definition_field_used_when_glossary_unmapped(self):
        # Parity with fresh mining: default config maps definition but not
        # glossary, so the block prepends to the DEFINITION field (a card-wide
        # <style> in any field), matching EpisodeProcessor._phase5_create.
        cfg = replace(_cfg(), anki_fields={**_cfg().anki_fields, "glossary": ""})
        def_field = cfg.anki_fields["definition"]
        svc = _svc([_note(1, BARE, field=def_field)])
        result = restyle_mined_cards(svc, cfg)
        assert result.restyled == 1
        (updates,), _ = svc.update_notes_fields.call_args
        nid, fields = updates[0]
        assert nid == 1
        assert def_field in fields
        assert fields[def_field].startswith("<style>")
        assert fields[def_field].endswith(BARE)  # prepend, original preserved

    def test_empty_block_is_noop(self, monkeypatch):
        # Defensive: an empty block would otherwise "restyle" every card forever.
        monkeypatch.setattr(card_restyler, "build_card_style_block", lambda **k: "")
        svc = MagicMock()
        result = restyle_mined_cards(svc, _cfg())
        assert result == RestyleResult(0, 0, 0, 0)
        svc.find_notes.assert_not_called()

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

    def test_block_computed_once(self, monkeypatch):
        collect = MagicMock(return_value="")
        monkeypatch.setattr(card_restyler, "collect_dictionary_css", collect)
        svc = _svc([_note(1, BARE), _note(2, BARE)])
        restyle_mined_cards(svc, _cfg())
        collect.assert_called_once()

    def test_find_notes_query_is_escaped(self):
        svc = _svc([])
        restyle_mined_cards(svc, replace(_cfg(), anki_note_type='Core_2k "x"'))
        (query,), _ = svc.find_notes.call_args
        assert query == 'note:"Core\\_2k \\"x\\""'


class TestRefreshBaseSheet:
    """Pure helper: swap a stale base head, preserve the dict_css tail + body
    verbatim. Since the Issue #93 tree-shaking the new base is computed per card
    by an injected ``base_for_html`` callable from the STAMPED body + the note's
    other miner field; the constant-base tests inject a constant callable."""

    @staticmethod
    def _refresh(value, new_base_or_fn, other_html=""):
        fn = new_base_or_fn if callable(new_base_or_fn) else (lambda html: new_base_or_fn)
        return card_restyler._refresh_base_sheet(value, other_html, fn)

    def test_not_style_prefixed_returns_none(self):
        assert self._refresh("<div>x</div>", "NB ol[data-count]{}") is None

    def test_missing_close_returns_none(self):
        # base token present but no </style> — must return None, never raise.
        assert self._refresh("<style>ol[data-count]{}", "NB ol[data-count]{}") is None

    def test_token_only_in_later_block_returns_none(self):
        v = "<style>.per-dict{}</style><style>ol[data-count]{}</style>body"
        assert self._refresh(v, "NB ol[data-count]{}") is None

    def test_newline_in_new_base_returns_none(self):
        # Belt-and-suspenders: the injected builder returning a newline-bearing
        # base (broken variant contract) degrades to a safe no-op — the guard
        # stays reachable precisely because the builder is injected.
        assert self._refresh("<style>OLD ol[data-count]{}</style>b", "a\nb") is None

    def test_swaps_base_preserves_multiline_dict_and_body(self):
        v = "<style>OLD ol[data-count]{}\n.dictA{}\n.dictB{}</style><div>body</div>"
        out = self._refresh(v, "NEW ol[data-count]{}")
        assert out == "<style>NEW ol[data-count]{}\n.dictA{}\n.dictB{}</style><div>body</div>"

    def test_no_dict_tail_swaps_base_only(self):
        v = "<style>OLD ol[data-count]{}</style><div>body</div>"
        out = self._refresh(v, "NEW ol[data-count]{}")
        assert out == "<style>NEW ol[data-count]{}</style><div>body</div>"

    def test_current_base_is_noop(self):
        v = "<style>NB ol[data-count]{}\n.dictA{}</style>body"
        assert self._refresh(v, "NB ol[data-count]{}") is None

    def test_idempotent_and_body_byte_identical(self):
        v = '<style>OLD ol[data-count]{}\n.d{}</style><li data-dictionary="X">x</li>'
        nb = "NEW ol[data-count]{}"
        r1 = self._refresh(v, nb)
        assert r1 is not None
        assert self._refresh(r1, nb) is None  # second pass is a no-op
        assert r1.split("</style>", 1)[1] == v.split("</style>", 1)[1]  # body preserved byte-for-byte

    def test_trailing_legacy_style_in_body_preserved(self):
        v = (
            "<style>OLD ol[data-count]{}\n.dictA{}</style>"
            '<div class="yomitan-glossary"><style>.legacy{}</style>'
            '<ol data-count="1"><li data-dictionary="X">x</li></ol></div>'
        )
        out = self._refresh(v, "NEW ol[data-count]{}")
        assert out is not None
        assert out.startswith("<style>NEW ol[data-count]{}\n.dictA{}</style>")
        assert "<style>.legacy{}</style>" in out  # body's own <style> survives verbatim

    def test_stamps_from_carried_dict_css_not_config(self):
        # Head carries scoped CSS for X; the body's unstamped envelope stamps from
        # the card's OWN CSS (no config is consulted — the helper takes no dict_css).
        v = (
            '<style>OLD ol[data-count]{}\n.yomitan-glossary [data-dictionary="X"]{color:red}</style>'
            '<div class="yomitan-glossary"><ol data-count="1"><li data-dictionary="X">x</li></ol></div>'
        )
        out = self._refresh(v, "NEW ol[data-count]{}")
        assert out is not None
        assert '<li data-dictionary="X" data-has-styles="">' in out

    def test_variant_computed_from_stamped_body(self):
        # With the REAL builder: an envelope governed only by the head's carried
        # dict_css tail must (a) end up stamped and (b) yield the CORE-ONLY
        # variant — the builder sees the post-stamp body. Witnessing pre-stamp
        # would wrongly pull the unstyled-dict groups in.
        tail = '.yomitan-glossary [data-dictionary="X"]{color:red}'
        v = f"<style>OLD ol[data-count]{{}}\n{tail}</style>{BARE}"
        out = self._refresh(v, _variant_for)
        assert out is not None
        assert out.startswith(f"<style>{base_css_variant(frozenset())}\n{tail}</style>")
        assert '<li data-dictionary="X" data-has-styles="">' in out

    def test_other_field_html_contributes_witnesses(self):
        # The note's other miner field participates in witness selection: a
        # gloss-image there pulls the images group into this field's head.
        v = f"<style>OLD ol[data-count]{{}}</style>{BARE}"
        out = self._refresh(v, _variant_for, other_html='<img class="gloss-image">')
        assert out is not None
        expected = base_css_variant(frozenset({"unstyled-chrome", "images"}))
        assert out.startswith(f"<style>{expected}</style>")


class TestLegacyEnvelopeStamping:
    """Legacy envelopes governed by scoped dictionary CSS get data-has-styles.

    Without the stamp, the prepended base sheet's gated gap-fillers apply and
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
        # contributes scoped CSS for that title into the prepended block — the
        # restyler injects that CSS, so the envelope must be gated too.
        monkeypatch.setattr(
            card_restyler,
            "collect_dictionary_css",
            lambda config: '.yomitan-glossary [data-dictionary="X"] li {color: red}',
        )
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
