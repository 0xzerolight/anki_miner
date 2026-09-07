"""The curator's sentence editor: what it parses, what it preselects, what it returns."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QPushButton

import anki_miner.gui.widgets.dialogs.sentence_edit_dialog as sed
from anki_miner.gui.widgets.dialogs.sentence_edit_dialog import SentenceEditDialog
from anki_miner.languages.registry import get_profile
from anki_miner.models import TokenizedWord

ORIGINAL = "時給系のスポーツは本当に苦手"
EDITED = "持久系のスポーツは本当に苦手"


def _token(surface: str, start: int, sentence: str, reading: str = "") -> TokenizedWord:
    return TokenizedWord(
        surface=surface,
        lemma=surface,
        reading=reading,
        sentence=sentence,
        start_time=0.0,
        end_time=0.0,
        duration=0.0,
        pos="名詞",
        surface_start=start,
        surface_end=start + len(surface),
        expression_reading=reading,
        mined_form_override=surface,
    )


PARSES = {
    ORIGINAL: [_token("時給", 0, ORIGINAL, "じきゅう"), _token("スポーツ", 4, ORIGINAL), _token("苦手", 13, ORIGINAL)],
    EDITED: [_token("持久", 0, EDITED, "じきゅう"), _token("スポーツ", 4, EDITED), _token("苦手", 13, EDITED)],
    "の": [],
}


def _parse(text: str) -> list[TokenizedWord]:
    return list(PARSES.get(text, []))


def _word() -> TokenizedWord:
    word = _token("時給", 0, ORIGINAL, "じきゅう")
    word.start_time, word.end_time, word.duration = 5.0, 7.0, 2.0
    return word


@pytest.fixture()
def sync_off_thread(monkeypatch):
    def fake_run_off_thread(parent, work, on_done, on_error=None, **kwargs):
        try:
            result = work()
        except Exception as exc:  # noqa: BLE001 - mirrors SingleCallWorker's error path
            if on_error is not None:
                on_error(str(exc))
            return MagicMock()
        on_done(result)
        return MagicMock()

    monkeypatch.setattr(sed, "run_off_thread", fake_run_off_thread)


@pytest.fixture()
def deferred_off_thread(monkeypatch):
    pending: list[tuple] = []

    def fake_run_off_thread(parent, work, on_done, on_error=None, **kwargs):
        pending.append((work, on_done, on_error))
        return MagicMock()

    monkeypatch.setattr(sed, "run_off_thread", fake_run_off_thread)
    return pending


def _dialog(qtbot, parse=_parse) -> SentenceEditDialog:
    dlg = SentenceEditDialog(_word(), parse_fn=parse, content_style=get_profile("ja").content_style)
    qtbot.addWidget(dlg)
    return dlg


def _rows(dlg: SentenceEditDialog) -> list[str]:
    return [dlg.word_list.item(i).text() for i in range(dlg.word_list.count())]


def _ok(dlg: SentenceEditDialog) -> QPushButton:
    button = dlg.buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert button is not None
    return button


def _retype(dlg: SentenceEditDialog, text: str) -> None:
    dlg.sentence_edit.setPlainText(text)
    dlg._request_parse()


class TestOpening:
    def test_seeds_the_sentence_and_preselects_the_word(self, qtbot, sync_off_thread):
        dlg = _dialog(qtbot)
        assert dlg.sentence_edit.toPlainText() == ORIGINAL
        assert _rows(dlg) == ["時給  じきゅう", "スポーツ", "苦手"]
        assert dlg.result_word().mined_form == "時給"
        assert _ok(dlg).isEnabled()

    def test_is_window_modal_with_no_default_button(self, qtbot, sync_off_thread):
        dlg = _dialog(qtbot)
        dlg.show()
        assert dlg.windowModality() == Qt.WindowModality.WindowModal
        assert not any(b.isDefault() for b in dlg.findChildren(QPushButton))


class TestReparse:
    def test_edited_sentence_lists_the_new_words_and_picks_the_nearest(self, qtbot, sync_off_thread):
        dlg = _dialog(qtbot)
        _retype(dlg, EDITED)
        assert _rows(dlg)[0] == "持久  じきゅう"
        assert dlg.result_word().mined_form == "持久"  # 時給 is gone; 持久 starts where it did

    def test_a_kept_word_stays_selected_across_a_reparse(self, qtbot, sync_off_thread):
        dlg = _dialog(qtbot)
        dlg.word_list.setCurrentRow(2)  # 苦手
        _retype(dlg, EDITED)
        assert dlg.result_word().mined_form == "苦手"

    def test_typing_disables_ok_until_the_parse_lands(self, qtbot, deferred_off_thread):
        dlg = _dialog(qtbot)
        work, on_done, _ = deferred_off_thread[0]
        on_done(work())
        assert _ok(dlg).isEnabled()
        dlg.sentence_edit.setPlainText(EDITED)
        assert not _ok(dlg).isEnabled()

    def test_no_mineable_word_disables_ok_and_says_so(self, qtbot, sync_off_thread):
        dlg = _dialog(qtbot)
        _retype(dlg, "の")
        assert _rows(dlg) == []
        assert dlg.result_word() is None
        assert not _ok(dlg).isEnabled()
        assert dlg.status_label.text()

    def test_newlines_join_with_a_space(self, qtbot, sync_off_thread):
        seen: list[str] = []

        def parse(text):
            seen.append(text)
            return _parse(text)

        dlg = _dialog(qtbot, parse)
        _retype(dlg, "持久系の\nスポーツ")
        assert seen[-1] == "持久系の スポーツ"

    def test_stale_parse_never_paints(self, qtbot, deferred_off_thread):
        dlg = _dialog(qtbot)
        first_work, first_done, _ = deferred_off_thread[0]
        dlg.sentence_edit.setPlainText(EDITED)
        dlg._request_parse()  # queued behind the in-flight original parse
        assert len(deferred_off_thread) == 1
        first_done(first_work())  # the ORIGINAL parse lands late
        assert len(deferred_off_thread) == 2  # the queued newest request was dispatched
        second_work, second_done, _ = deferred_off_thread[1]
        second_done(second_work())
        assert _rows(dlg)[0] == "持久  じきゅう"

    def test_parse_failure_reports_and_disables_ok(self, qtbot, sync_off_thread):
        def parse(text):
            raise RuntimeError("engine gone")

        dlg = _dialog(qtbot, parse)
        assert dlg.result_word() is None
        assert not _ok(dlg).isEnabled()
        assert dlg.status_label.text()


class TestPreviewAndResult:
    def test_preview_bolds_the_chosen_word(self, qtbot, sync_off_thread):
        dlg = _dialog(qtbot)
        dlg.word_list.setCurrentRow(1)
        assert "<b>スポーツ</b>" in dlg.preview_label.text()

    def test_result_is_the_parsed_token_with_its_span(self, qtbot, sync_off_thread):
        dlg = _dialog(qtbot)
        _retype(dlg, EDITED)
        token = dlg.result_word()
        assert (token.sentence, token.surface_start, token.surface_end) == (EDITED, 0, 2)

    def test_bare_return_does_not_accept(self, qtbot, sync_off_thread):
        """An IME commits kana with Return; the dialog must not treat it as Confirm."""
        dlg = _dialog(qtbot)
        dlg.show()
        dlg.sentence_edit.setFocus()
        qtbot.keyClick(dlg.sentence_edit, Qt.Key.Key_Return)
        assert dlg.result() == QDialog.DialogCode.Rejected
        assert dlg.isVisible()

    def test_primary_action_accepts_only_when_ready(self, qtbot, sync_off_thread):
        dlg = _dialog(qtbot)
        _retype(dlg, "の")
        dlg._accept_if_ready()
        assert dlg.result() == QDialog.DialogCode.Rejected
        _retype(dlg, EDITED)
        dlg._accept_if_ready()
        assert dlg.result() == QDialog.DialogCode.Accepted
