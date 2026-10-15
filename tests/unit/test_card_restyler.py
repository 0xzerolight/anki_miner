"""Tests for Restyle Mined Cards: converting old cards to Yomitan's export shape (card_restyler).

Every historical body shape converts: v2.4.0-v2.6.7 ``span.gloss-content``
bodies, v2.7.0-v2.7.7 per-dictionary ``<style>`` inside the wrapper, v2.7.8+
single-carrier LEADING house blocks, and per-field trailing house blocks. The
house base sheet goes, carried dictionary CSS stays, and a field that cannot be
converted without loss is left untouched.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from anki_miner.config import create_default_config
from anki_miner.services import card_restyler
from anki_miner.services.card_restyler import RestyleResult, _convert_field, restyle_mined_cards

OPEN = '<div style="text-align: left;" class="yomitan-glossary"><ol>'
CLOSE = "</ol></div>"
OWNED = ".yomitan-glossary{--anki-miner-owned-style: 1;--am-muted: x}.yomitan-glossary ol[data-count]{margin:0}"
LEGACY_OWNED = ".yomitan-glossary{--am-muted: rgba(128,128,128,0.95);}.yomitan-glossary ol[data-count]{margin:0}"
RTL_LEGACY = '.yomitan-glossary ol[data-count] [data-sc-content="example-sentence-a"]{unicode-bidi:plaintext}'
CARRIED = '.yomitan-glossary [data-dictionary="X"] b{color:red}'

# A v2.7.0+ body (div content), as every current card carries it.
BODY = (
    '<div class="yomitan-glossary"><ol data-count="1"><li data-dictionary="X"><i>(n, X)</i>'
    '<ul class="gloss-list" data-count="1"><li class="gloss-item"><div class="gloss-content">cat</div></li></ul>'
    "</li></ol></div>"
)
CONVERTED = OPEN + '<li data-dictionary="X"><i>(n, X)</i> cat</li>' + CLOSE
# v2.4.0-v2.6.7: span content, one gloss-item per glossary member.
BODY_V26 = (
    '<div class="yomitan-glossary"><ol data-count="1"><li data-dictionary="X"><i>(v1, X)</i>'
    '<ul class="gloss-list" data-count="2"><li class="gloss-item"><span class="gloss-content">to eat</span></li>'
    '<li class="gloss-item"><span class="gloss-content">to consume</span></li></ul></li></ol></div>'
)
# v2.7.0-v2.7.7: the dictionary's own <style> inside the wrapper.
DICT_STYLED = BODY.replace('<ol data-count="1">', f'<style>{CARRIED}</style><ol data-count="1">')
YOMITAN_EXPORT = '<div style="text-align: left;" class="yomitan-glossary"><ol><li data-dictionary="Y">x</li></ol></div>'
LEGACY_LEADING = f"<style>{LEGACY_OWNED}</style>{BODY}"
CURRENT_TRAILING = f"{BODY}<style>{OWNED}\n{CARRIED}</style>"


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


def _written(svc, field="Glossary"):
    (updates,), _ = svc.update_notes_fields.call_args
    return updates[0][1][field]


@pytest.fixture(autouse=True)
def _no_disk_io(monkeypatch):
    """Avoid real dictionary-registry / SQLite I/O."""
    monkeypatch.setattr(card_restyler, "collect_dictionary_css_entries", lambda config: [])


class TestConvertField:
    def test_trailing_house_block_dropped_carried_css_kept(self):
        assert _convert_field(CURRENT_TRAILING, [], "ltr") == CONVERTED.replace(
            CLOSE, f"<style>{CARRIED}</style>{CLOSE}"
        )

    def test_carried_css_survives_with_no_dictionary_installed(self):
        assert CARRIED in _convert_field(CURRENT_TRAILING, [], "ltr")

    def test_legacy_leading_block_and_rtl_rule_are_stripped(self):
        value = f"<style>{LEGACY_OWNED}\n{CARRIED}\n{RTL_LEGACY}</style>{BODY}"
        out = _convert_field(value, [], "rtl")
        assert "--am-muted" not in out and "ol[data-count]" not in out
        assert out.endswith(f"<style>{CARRIED}</style>{CLOSE}")

    def test_rtl_card_keeps_its_bidi_when_converted_under_an_ltr_language(self):
        body = BODY.replace(">cat<", '><div class="gloss-sc-div" data-sc-content="example-sentence-a">مثال</div><')
        out = _convert_field(f"{body}<style>{OWNED}\n{RTL_LEGACY}</style>", [], "ltr")
        assert 'style="unicode-bidi: plaintext;"' in out

    def test_control_characters_do_not_abort_the_run(self):
        assert _convert_field(BODY.replace("cat", "c\x0bat"), [], "ltr") == CONVERTED

    def test_refused_notes_are_logged(self, caplog):
        svc = _svc([_note(7, f"<style>{OWNED}</style>{BODY}<style>{OWNED}</style>")])
        with caplog.at_level("INFO", logger="anki_miner.services.card_restyler"):
            restyle_mined_cards(svc, _cfg())
        assert "[7]" in caplog.text

    def test_block_without_dictionary_tail_leaves_no_style(self):
        assert _convert_field(f"{BODY}<style>{OWNED}</style>", [], "ltr") == CONVERTED

    def test_bare_body_gets_current_config_css(self):
        out = _convert_field(BODY, [("x-id", "X", CARRIED)], "ltr")
        assert out == CONVERTED.replace(CLOSE, f"<style>{CARRIED}</style>{CLOSE}")

    def test_v26_span_body_converts(self):
        assert _convert_field(f"{BODY_V26}<style>{OWNED}</style>", [], "ltr") == (
            OPEN + '<li data-dictionary="X"><i>(v1, X)</i> to eat</li><li data-dictionary="X">to consume</li>' + CLOSE
        )

    def test_v270_dict_style_inside_wrapper_is_kept_once(self):
        out = _convert_field(DICT_STYLED, [("x-id", "X", CARRIED)], "ltr")
        assert out.endswith(f"<style>{CARRIED}</style>{CLOSE}")
        assert out.count("<style>") == 1

    def test_hand_text_survives(self):
        assert _convert_field(f"{BODY}<style>{OWNED}</style>my note", [], "ltr").endswith(CLOSE + "my note")

    def test_two_house_blocks_are_left_untouched(self):
        assert _convert_field(f"<style>{OWNED}</style>{BODY}<style>{OWNED}</style>", [], "ltr") is None

    def test_unclosed_house_block_is_left_untouched(self):
        assert _convert_field(BODY + f"<style>{OWNED}", [], "ltr") is None

    def test_unknown_body_shape_is_left_untouched(self):
        # Internal markup the pass yields nothing from: never strip the house
        # block without converting the body.
        odd = f'<div class="yomitan-glossary"><ol data-count="1"><li>loose</li></ol></div><style>{OWNED}</style>'
        assert _convert_field(odd, [], "ltr") is None

    def test_visible_text_loss_is_refused(self, monkeypatch):
        monkeypatch.setattr(card_restyler, "to_yomitan_export", lambda html, direction: CONVERTED.replace("cat", "ca"))
        assert _convert_field(BODY, [], "ltr") is None


class TestRestyleMinedCards:
    def test_converts_and_counts(self):
        svc = _svc([_note(1, CURRENT_TRAILING)])
        assert restyle_mined_cards(svc, _cfg()) == RestyleResult(1, 1, 0, 0)
        assert _written(svc) == CONVERTED.replace(CLOSE, f"<style>{CARRIED}</style>{CLOSE}")

    def test_rerun_is_up_to_date(self):
        svc = _svc([_note(1, LEGACY_LEADING)])
        restyle_mined_cards(svc, _cfg())
        svc2 = _svc([_note(1, _written(svc))])
        assert restyle_mined_cards(svc2, _cfg()) == RestyleResult(1, 0, 1, 0)
        svc2.update_notes_fields.assert_not_called()

    def test_yomitan_card_is_up_to_date_and_untouched(self):
        svc = _svc([_note(1, YOMITAN_EXPORT)])
        assert restyle_mined_cards(svc, _cfg()) == RestyleResult(1, 0, 1, 0)
        svc.update_notes_fields.assert_not_called()

    def test_refused_field_is_left_alone(self):
        svc = _svc([_note(1, f"<style>{OWNED}</style>{BODY}<style>{OWNED}</style>")])
        assert restyle_mined_cards(svc, _cfg()) == RestyleResult(1, 0, 1, 0)
        svc.update_notes_fields.assert_not_called()

    def test_counts_exact_tuple(self):
        notes = [
            _note(1, LEGACY_LEADING),  # converted
            _note(2, BODY),  # bare internal body: converted
            _note(3, YOMITAN_EXPORT),  # already Yomitan-shaped
            _note(4, "<div>plain</div>"),  # not a glossary card
            _note(5, BODY_V26),  # v2.6 body: converted
        ]
        assert restyle_mined_cards(_svc(notes), _cfg()) == RestyleResult(5, 3, 1, 1)

    def test_no_writer_emits_leading_style(self):
        notes = [_note(1, LEGACY_LEADING), _note(2, DICT_STYLED), _note(3, CURRENT_TRAILING)]
        svc = _svc(notes)
        restyle_mined_cards(svc, _cfg())
        (updates,), _ = svc.update_notes_fields.call_args
        for _nid, fields in updates:
            for value in fields.values():
                assert value.startswith(OPEN) and value.endswith(CLOSE)

    def test_partial_update_counts_failed_and_preserves_counter_sum(self):
        svc = _svc([_note(1, BODY), _note(2, BODY)])
        svc.update_notes_fields.side_effect = None
        svc.update_notes_fields.return_value = [1]
        result = restyle_mined_cards(svc, _cfg())
        assert (result.restyled, result.failed) == (1, 1)
        assert result.restyled + result.failed + result.skipped_styled + result.skipped_no_markup == result.scanned

    def test_both_mapped_fields_converted_in_one_write(self):
        cfg = _cfg()
        def_field = cfg.anki_fields["definition"]
        note = {"noteId": 1, "fields": {"Glossary": {"value": LEGACY_LEADING}, def_field: {"value": BODY}}}
        svc = _svc([note])
        assert restyle_mined_cards(svc, cfg) == RestyleResult(1, 1, 0, 0)
        (updates,), _ = svc.update_notes_fields.call_args
        assert len(updates) == 1
        assert updates[0][1] == {"Glossary": CONVERTED, def_field: CONVERTED}

    def test_mixed_note_counts_restyled_once(self):
        cfg = _cfg()
        def_field = cfg.anki_fields["definition"]
        note = {"noteId": 1, "fields": {"Glossary": {"value": CONVERTED}, def_field: {"value": BODY}}}
        svc = _svc([note])
        assert restyle_mined_cards(svc, cfg) == RestyleResult(1, 1, 0, 0)
        (updates,), _ = svc.update_notes_fields.call_args
        assert list(updates[0][1].keys()) == [def_field]

    def test_no_glossary_field_mapped_is_noop(self):
        cfg = replace(_cfg(), anki_fields={**_cfg().anki_fields, "glossary": "", "definition": ""})
        svc = MagicMock()
        assert restyle_mined_cards(svc, cfg) == RestyleResult(0, 0, 0, 0)
        svc.find_notes.assert_not_called()

    def test_definition_field_processed_when_glossary_unmapped(self):
        cfg = replace(_cfg(), anki_fields={**_cfg().anki_fields, "glossary": ""})
        def_field = cfg.anki_fields["definition"]
        svc = _svc([_note(1, BODY, field=def_field)])
        assert restyle_mined_cards(svc, cfg).restyled == 1
        assert _written(svc, def_field) == CONVERTED

    def test_deleted_and_missing_field_skipped(self):
        notes = [{}, {"noteId": 2, "fields": {"Other": {"value": "x"}}}, _note(3, BODY)]
        svc = MagicMock()
        svc.find_notes.return_value = [1, 2, 3]
        svc.notes_info.return_value = notes
        svc.update_notes_fields.side_effect = lambda updates: [note_id for note_id, _fields in updates]
        assert restyle_mined_cards(svc, _cfg()).restyled == 1

    def test_cancel_stops_after_first_chunk(self, monkeypatch):
        monkeypatch.setattr(card_restyler, "_CHUNK", 1)
        svc = MagicMock()
        svc.find_notes.return_value = [1, 2]
        svc.notes_info.side_effect = lambda ids: [_note(ids[0], BODY)]
        svc.update_notes_fields.side_effect = lambda updates: [note_id for note_id, _fields in updates]
        state = {"n": 0}

        def is_cancelled():
            state["n"] += 1
            return state["n"] > 1

        result = restyle_mined_cards(svc, _cfg(), is_cancelled=is_cancelled)
        assert svc.notes_info.call_count == 1
        assert result.restyled == 1

    def test_css_entries_collected_once(self, monkeypatch):
        collect = MagicMock(return_value=[])
        monkeypatch.setattr(card_restyler, "collect_dictionary_css_entries", collect)
        restyle_mined_cards(_svc([_note(1, BODY), _note(2, BODY)]), _cfg())
        collect.assert_called_once()

    def test_find_notes_query_is_escaped(self):
        svc = _svc([])
        restyle_mined_cards(svc, replace(_cfg(), anki_note_type='Core_2k "x"'))
        (query,), _ = svc.find_notes.call_args
        assert query == 'note:"Core\\_2k \\"x\\""'


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
