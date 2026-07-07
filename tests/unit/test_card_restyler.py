"""Tests for the one-time Restyle Mined Cards service (card_restyler)."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from anki_miner.config import create_default_config
from anki_miner.services import card_restyler
from anki_miner.services.card_restyler import RestyleResult, restyle_mined_cards

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

    def test_already_styled_skipped(self):
        svc = _svc([_note(1, ALREADY)])
        result = restyle_mined_cards(svc, _cfg())
        assert result.restyled == 0
        assert result.skipped_styled == 1
        svc.update_notes_fields.assert_not_called()

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
