"""A03: while the Word Curator is open the run says it is waiting for the user."""

from __future__ import annotations

from time import monotonic
from unittest.mock import patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QDialog

from anki_miner.gui.controllers.task_registry import TaskRegistry

MODULE = "anki_miner.gui.widgets._mining_tab_base"


class _FakeCurator(QDialog):
    """A real QDialog standing in for WordCurationDialog (finished/destroyed behave)."""

    def __init__(self, words, parent=None, **kwargs):
        super().__init__(parent)
        self.words = list(words)

    def force_reject(self):
        self.reject()

    def get_selected_words(self):
        return list(self.words)


@pytest.fixture
def registry(qtbot):
    reg = TaskRegistry()
    yield reg
    reg.shutdown()


def _open_review(tab, registry):
    tab.bind_task_registry(registry)
    tab._publish_task_start("Single episode")
    tab._publish_task_stage(2, 5, "Filtering against known vocabulary")
    with patch(f"{MODULE}.WordCurationDialog", _FakeCurator):
        tab._show_curation_dialog(["w1"], None, None, None)
    dialog = tab._active_curation_dialog
    assert dialog is not None
    return dialog


def test_the_stage_names_the_review(single_tab, registry):
    _open_review(single_tab, registry)

    snapshot = registry.snapshot(single_tab.TASK_ID)
    assert snapshot is not None
    assert snapshot.stage_name == "Waiting for your word review"
    assert "Waiting for your word review" in single_tab.action_bar.stage_label.full_text


def test_show_review_sits_in_the_pinned_bar_while_reviewing(single_tab, registry):
    _open_review(single_tab, registry)

    button = single_tab._show_review_button
    assert button is not None
    assert button in single_tab.action_bar.current_secondary()
    assert not button.isHidden()
    assert button.text() == "Show review"


def test_closing_the_review_restores_the_stage_and_the_bar(single_tab, registry):
    dialog = _open_review(single_tab, registry)
    before = single_tab.action_bar.current_secondary()

    dialog.reject()

    snapshot = registry.snapshot(single_tab.TASK_ID)
    assert snapshot is not None
    assert snapshot.stage_name == "Filtering against known vocabulary"
    assert snapshot.stage_index == 2
    button = single_tab._show_review_button
    assert button not in single_tab.action_bar.current_secondary()
    assert button.isHidden()
    assert single_tab.action_bar.current_secondary() == tuple(b for b in before if b is not button)


def test_the_review_is_not_a_stall(single_tab, registry):
    _open_review(single_tab, registry)

    registry.tick(now=monotonic() + 600.0)

    snapshot = registry.snapshot(single_tab.TASK_ID)
    assert snapshot is not None
    assert snapshot.no_update_age_s == 0.0


def test_show_review_raises_the_curator(single_tab, registry, monkeypatch):
    dialog = _open_review(single_tab, registry)
    raised: list[str] = []
    monkeypatch.setattr(dialog, "raise_", lambda: raised.append("raise"))
    monkeypatch.setattr(dialog, "activateWindow", lambda: raised.append("activate"))

    single_tab._show_review_button.click()

    assert raised == ["raise", "activate"]


def test_a_queue_screen_without_a_stage_shows_the_bare_words(audiobook_tab, registry):
    audiobook_tab.bind_task_registry(registry)
    audiobook_tab._publish_task_start("Audiobook mining", total=2)
    with patch(f"{MODULE}.WordCurationDialog", _FakeCurator):
        audiobook_tab._show_curation_dialog(["w1"], None, None, None)

    assert audiobook_tab.action_bar.stage_label.full_text == "Waiting for your word review"
    audiobook_tab._active_curation_dialog.reject()
    snapshot = registry.snapshot(audiobook_tab.TASK_ID)
    assert snapshot is not None
    assert snapshot.stage_name == ""
