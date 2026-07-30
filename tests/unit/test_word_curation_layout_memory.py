"""The curator remembers its window and its splits between queue items.

A mining queue builds a fresh ``WordCurationDialog`` per item, so a user who
widens the video column used to widen it again for every word. The state lives
in the machine-local ``ui_state.ini`` (D7: never in an exported config or a
profile), keyed by the side column's pane composition so a manga curator cannot
restore a video curator's sizes.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtCore import QByteArray, Qt
from PyQt6.QtWidgets import QDialog, QSplitter

from anki_miner.gui.utils import session_state
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog


@pytest.fixture(autouse=True)
def state_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the session store at a throwaway home for every test here."""
    monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", tmp_path / "gui_config.json")
    return tmp_path


def _lookup(term: str) -> list[tuple[str, str]]:
    return [("JMdict", f"a definition of {term}")]


def _build(qtbot, words, **kwargs) -> WordCurationDialog:
    dlg = WordCurationDialog(words, **kwargs)
    qtbot.addWidget(dlg)
    dlg.resize(1500, 800)
    dlg.show()
    return dlg


def _main_splitter(dlg: WordCurationDialog) -> QSplitter:
    return [s for s in dlg.findChildren(QSplitter) if s.orientation() == Qt.Orientation.Horizontal][0]


class TestTheDragSurvivesTheNextItem:
    def test_a_dragged_split_comes_back(self, qtbot, make_tokenized_words):
        first = _build(qtbot, make_tokenized_words(4), lookup_fn=_lookup)
        _main_splitter(first).setSizes([500, 900])
        dragged = _main_splitter(first).sizes()
        first.accept()

        second = _build(qtbot, make_tokenized_words(4), lookup_fn=_lookup)
        assert _main_splitter(second).sizes() == dragged

    def test_the_window_size_comes_back(self, qtbot, make_tokenized_words):
        """Asserted on height: ``restoreGeometry`` clamps to the screen, and
        the offscreen platform's screen is narrower than this dialog's own
        minimum width, so width can never round-trip in a headless run.
        """
        first = _build(qtbot, make_tokenized_words(4), lookup_fn=_lookup)
        first.resize(first.width(), 640)
        first.accept()

        # Built without the fixture's resize: the restored geometry is what
        # this asserts, and a later resize() would be what it measured.
        second = WordCurationDialog(make_tokenized_words(4), lookup_fn=_lookup)
        qtbot.addWidget(second)
        second.show()
        assert second.height() == 640

    def test_cancelling_saves_it_too(self, qtbot, make_tokenized_words):
        """Rejecting abandons the review, not the user's window."""
        first = _build(qtbot, make_tokenized_words(4), lookup_fn=_lookup)
        _main_splitter(first).setSizes([620, 780])
        dragged = _main_splitter(first).sizes()
        first.reject()

        second = _build(qtbot, make_tokenized_words(4), lookup_fn=_lookup)
        assert _main_splitter(second).sizes() == dragged

    def test_a_forced_reject_saves_it_too(self, qtbot, make_tokenized_words):
        """Teardown and shutdown go through force_reject, not reject."""
        first = _build(qtbot, make_tokenized_words(4), lookup_fn=_lookup)
        first.force_reject()
        assert session_state.load_curator_layout(first._side_key)[0] is not None

    def test_the_save_happens_once(self, qtbot, make_tokenized_words):
        dlg = _build(qtbot, make_tokenized_words(4), lookup_fn=_lookup)
        dlg.accept()
        dlg.reject()
        assert dlg._layout_state_saved is True


class TestRestoreIsDefensive:
    def test_a_corrupt_split_blob_falls_back_to_the_default_ratio(self, qtbot, make_tokenized_words):
        session_state.save_curator_layout(
            QByteArray(b"geo"), QByteArray(b"not a splitter state"), None, side_key="sentences+dict"
        )
        dlg = _build(qtbot, make_tokenized_words(4), lookup_fn=_lookup)
        left, right = _main_splitter(dlg).sizes()
        assert 0.3 < right / (left + right) < 0.5

    def test_restoring_cannot_re_enable_collapsing(self, qtbot, make_tokenized_words):
        """restoreState carries childrenCollapsible along with the sizes."""
        first = _build(qtbot, make_tokenized_words(4), lookup_fn=_lookup)
        splitter = _main_splitter(first)
        splitter.setChildrenCollapsible(True)
        first.accept()

        second = _build(qtbot, make_tokenized_words(4), lookup_fn=_lookup)
        assert _main_splitter(second).childrenCollapsible() is False

    def test_a_table_only_curator_saves_nothing_it_cannot_restore(self, qtbot, make_tokenized_words):
        dlg = WordCurationDialog(make_tokenized_words(3))
        qtbot.addWidget(dlg)
        dlg.show()
        dlg.accept()
        geometry, main_split, side_split = session_state.load_curator_layout(dlg._side_key)
        assert geometry is not None
        assert main_split is None
        assert side_split is None


class TestTheDialogStillBehaves:
    def test_done_still_reports_its_result(self, qtbot, make_tokenized_words):
        dlg = _build(qtbot, make_tokenized_words(3), lookup_fn=_lookup)
        results: list[int] = []
        dlg.finished.connect(results.append)
        dlg.accept()
        assert results == [QDialog.DialogCode.Accepted.value]

    def test_a_failed_save_does_not_stop_the_dialog_closing(self, qtbot, make_tokenized_words, monkeypatch):
        """Remembering the layout is a convenience. A queue item waits on this
        dialog closing, so no failure here may hold it open.
        """

        def _explode(*_args, **_kwargs):
            raise OSError("read-only home")

        monkeypatch.setattr(session_state, "save_curator_layout", _explode)
        dlg = _build(qtbot, make_tokenized_words(3), lookup_fn=_lookup)
        dlg.accept()
        assert dlg.isVisible() is False
        assert dlg.result() == QDialog.DialogCode.Accepted.value
