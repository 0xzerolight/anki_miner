"""The Find a Feature browser dialog: filtering + selection + navigation."""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from PyQt6.QtCore import QCoreApplication, QTranslator
from PyQt6.QtWidgets import QPushButton

from anki_miner.gui.capabilities import CAPABILITIES, CapabilityTarget
from anki_miner.gui.widgets.dialogs.capability_browser import (
    CapabilityBrowser,
    run_capability_browser,
)


@pytest.fixture
def dialog(qtbot):
    dlg = CapabilityBrowser()
    qtbot.addWidget(dlg)
    yield dlg
    dlg.deleteLater()


def test_starts_showing_everything(dialog):
    assert dialog._current == list(CAPABILITIES)


def test_typing_filters_to_matches(dialog):
    dialog.search_box.setText("i+1")
    shown = {c.id for c in dialog._current}
    assert "i-plus-one" in shown
    assert "youtube-mining" not in shown


def test_typing_displayed_localized_title_finds_capability(qapp, qtbot):
    class _SpanishTranslator(QTranslator):
        def translate(self, context, source, disambiguation=None, n=-1):  # noqa: N802
            if context == "Capabilities" and source == "Mine a single episode":
                return "Minar un solo episodio"
            return source

    translator = _SpanishTranslator()
    qapp.installTranslator(translator)
    try:
        dlg = CapabilityBrowser()
        qtbot.addWidget(dlg)
        displayed_title = QCoreApplication.translate("Capabilities", "Mine a single episode")

        dlg.search_box.setText(displayed_title)

        assert displayed_title == "Minar un solo episodio"
        assert [cap.id for cap in dlg._current] == ["episode-mining"]
    finally:
        qapp.removeTranslator(translator)


def test_open_buttons_match_visible_rows(dialog):
    dialog.search_box.setText("youtube")
    buttons = [b for b in dialog.findChildren(QPushButton) if b.objectName() == "capability-open"]
    assert len(buttons) == len(dialog._current)


def test_no_match_shows_empty_state(dialog):
    dialog.search_box.setText("zzzz-nothing-here")
    assert dialog._current == []
    assert dialog._empty_label.isVisible() or dialog._empty_label.isVisibleTo(dialog)


def test_clearing_search_restores_full_list(dialog):
    dialog.search_box.setText("pitch")
    assert len(dialog._current) < len(CAPABILITIES)
    dialog.search_box.setText("")
    assert dialog._current == list(CAPABILITIES)


def test_choosing_records_target_and_accepts(dialog, qtbot):
    target = CapabilityTarget("settings", "filtering")
    cap = next(c for c in CAPABILITIES if c.target == target)
    with qtbot.waitSignal(dialog.accepted, timeout=1000):
        dialog._choose(cap)
    assert dialog.selected_target == target


def test_clicking_open_button_selects_that_row(dialog, qtbot):
    dialog.search_box.setText("audiobook")  # narrows to a single row
    visible = dialog._current
    assert len(visible) == 1
    button = next(b for b in dialog.findChildren(QPushButton) if b.objectName() == "capability-open")
    with qtbot.waitSignal(dialog.accepted, timeout=1000):
        button.click()
    assert dialog.selected_target == visible[0].target


def test_runner_navigates_on_selection(qtbot, monkeypatch):
    main_window = Mock()
    target = CapabilityTarget("video")

    def fake_exec(self):
        self.selected_target = target
        return 1

    monkeypatch.setattr(CapabilityBrowser, "exec", fake_exec)
    run_capability_browser(None, main_window)
    main_window.reveal_capability.assert_called_once_with(target)


def test_runner_noops_when_dismissed(qtbot, monkeypatch):
    main_window = Mock()

    def fake_exec(self):
        self.selected_target = None
        return 0

    monkeypatch.setattr(CapabilityBrowser, "exec", fake_exec)
    run_capability_browser(None, main_window)
    main_window.reveal_capability.assert_not_called()
