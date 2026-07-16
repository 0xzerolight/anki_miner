"""Tests for the Text sub-tab of the Reading tab.

``ReadingTextTab`` mines one pasted-text snippet per run over the shared
``_ReadingMiningTabBase`` lifecycle — a single ephemeral ``ReadingQueueItem``
carrying a pathless ``kind="text"`` ref (the text is snapshotted at Mine time).
Behaviour under test:

* Mine enablement is derived from the text edit: blank/whitespace disables it.
* Start: exactly one ephemeral item, ``source.text`` = the pasted snapshot,
  ``source.path`` is None, identity title "Text".
* Per-item signals are READ-ONLY on item state (the worker owns the lifecycle).
* Cleanup restores the Cancel button and retains the pasted text.
* Curation context has no media (``None``) but wires the definition-pane
  ``lookup_fn`` from the worker's ``curation_processor``.

Qt threads are never started — ``ReadingQueueWorker`` is class-level patched at
the base module so ``start()`` is a no-op and constructor kwargs can be
inspected. ``detect`` is never patched: this tab builds its ref directly.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.reading_text_tab import ReadingTextTab

_WORKER_TARGET = "anki_miner.gui.widgets._reading_mining_base.ReadingQueueWorker"


@pytest.fixture
def tab(qtbot, test_config: AnkiMinerConfig):
    """Instantiate a ReadingTextTab with the queue worker class patched."""
    with patch(_WORKER_TARGET, autospec=False) as queue_cls:
        queue_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")

        widget = ReadingTextTab(
            config=test_config,
            processor=MagicMock(name="EpisodeProcessor"),
            presenter=MagicMock(name="Presenter"),
        )
        qtbot.addWidget(widget)
        widget._queue_worker_cls = queue_cls  # type: ignore[attr-defined]
        try:
            yield widget
        finally:
            widget.deleteLater()


def _mine(tab, text: str = "本文です。"):
    """Paste *text* and click Mine."""
    tab.text_edit.setPlainText(text)
    tab._on_mine_clicked()


class TestInitialState:
    """Idle tab: Mine visible but disabled on empty text, Cancel hidden."""

    def test_buttons_idle(self, tab):
        assert not tab.mine_button.isHidden()
        assert not tab.mine_button.isEnabled()  # empty edit
        assert tab.cancel_button.isHidden()
        assert tab.worker_thread is None

    def test_review_checkbox_default_unchecked(self, tab):
        assert tab.review_words_checkbox.isChecked() is False

    def test_section_header_says_pasted_text(self, tab):
        from anki_miner.gui.widgets.enhanced import SectionHeader

        headers = tab.findChildren(SectionHeader)
        assert any(h.title_label.text() == "Pasted Text" for h in headers)


class TestMineEnablement:
    """Mine is derived from the text edit content."""

    def test_text_enables_mine(self, tab):
        tab.text_edit.setPlainText("本文")
        assert tab.mine_button.isEnabled()

    def test_clearing_disables_mine(self, tab):
        tab.text_edit.setPlainText("本文")
        tab.text_edit.clear()
        assert not tab.mine_button.isEnabled()

    def test_whitespace_only_keeps_mine_disabled(self, tab):
        tab.text_edit.setPlainText("   \n\t  ")
        assert not tab.mine_button.isEnabled()


class TestStartRun:
    """Mine launches one ephemeral pathless-text item."""

    def test_mine_builds_single_text_item(self, tab):
        queue_cls = tab._queue_worker_cls
        _mine(tab, "今日は晴れ。")

        assert queue_cls.call_count == 1
        items = queue_cls.call_args.kwargs["items"]
        assert len(items) == 1
        item = items[0]
        assert item.kind == "text"
        assert item.title == "Text"
        assert item.source.kind == "text"
        assert item.source.path is None
        assert item.source.text == "今日は晴れ。"
        assert tab.worker_thread is not None
        tab.worker_thread.start.assert_called_once()

    def test_blank_forced_click_warns_no_run(self, tab):
        queue_cls = tab._queue_worker_cls
        tab._on_mine_clicked()
        queue_cls.assert_not_called()
        assert "Paste some text" in tab.log_widget.text_edit.toPlainText()

    def test_run_refused_while_worker_active(self, tab):
        queue_cls = tab._queue_worker_cls
        _mine(tab)
        assert queue_cls.call_count == 1
        _mine(tab, "別の文。")
        assert queue_cls.call_count == 1  # second click refused

    def test_curation_callback_gated_on_checkbox(self, tab):
        queue_cls = tab._queue_worker_cls
        tab.review_words_checkbox.setChecked(True)
        _mine(tab)
        assert queue_cls.call_args.kwargs["curation_callback"] == tab._curation_bridge

    def test_curation_callback_none_when_unchecked(self, tab):
        queue_cls = tab._queue_worker_cls
        _mine(tab)
        assert queue_cls.call_args.kwargs["curation_callback"] is None

    def test_start_resets_bar_and_swaps_buttons(self, tab):
        _mine(tab)
        assert tab.mine_button.isHidden()
        assert not tab.cancel_button.isHidden()
        assert "Starting" in tab.overall_progress_widget.status_label.text()

    def test_text_stays_editable_during_run(self, tab):
        _mine(tab, "元の文。")
        assert tab.text_edit.isEnabled()
        # The ref snapshotted the text at Mine time; edits don't touch it.
        tab.text_edit.setPlainText("変更後。")
        items = tab._queue_worker_cls.call_args.kwargs["items"]
        assert items[0].source.text == "元の文。"


class TestItemSlots:
    """Per-item slots are READ-ONLY on item state and drive the run bar."""

    def test_item_started_sets_status(self, tab):
        _mine(tab)
        tab._on_item_started(0)
        assert "Mining pasted text" in tab.overall_progress_widget.status_label.text()

    def test_item_progress_drives_bar(self, tab):
        _mine(tab)
        tab._on_item_started(0)
        tab._on_item_progress(0, "Definitions", 50)
        assert tab.overall_progress_widget.progress_bar.value() == 50

    def test_item_progress_negative_pct_holds_bar(self, tab):
        _mine(tab)
        tab._on_item_started(0)
        tab._on_item_progress(0, "Working", 50)
        before = tab.overall_progress_widget.progress_bar.value()
        tab._on_item_progress(0, "Still working", -1)
        assert tab.overall_progress_widget.progress_bar.value() == before

    def test_item_finished_success_logs_and_forwards(self, tab):
        _mine(tab)
        result = MagicMock(cards_created=7)
        tab._on_item_finished(0, result, None, 1)
        assert "7 cards" in tab.log_widget.text_edit.toPlainText()
        tab._presenter.show_processing_result.assert_called_once_with(result)

    def test_item_finished_error_logged(self, tab):
        _mine(tab)
        tab._on_item_finished(0, None, "boom", 1)
        assert "boom" in tab.log_widget.text_edit.toPlainText()

    def test_item_finished_does_not_write_state(self, tab):
        _mine(tab)
        item = tab._run_items[0]
        status_before = item.status
        tab._on_item_finished(0, MagicMock(cards_created=1), None, 1)
        assert item.status is status_before  # slot never writes item state

    def test_out_of_range_idx_is_noop(self, tab):
        _mine(tab)
        tab._on_item_started(99)
        tab._on_item_finished(99, None, "x", 1)  # must not raise

    def test_queue_finished_is_noop(self, tab):
        _mine(tab)
        before = tab.log_widget.text_edit.toPlainText()
        tab._on_queue_finished()
        assert tab.log_widget.text_edit.toPlainText() == before


class TestCleanup:
    """Cleanup restores buttons and retains the pasted text."""

    def test_cleanup_restores_buttons_and_keeps_text(self, tab):
        _mine(tab, "残る文。")
        tab._on_worker_finished()
        assert not tab.mine_button.isHidden()
        assert tab.mine_button.isEnabled()  # text still present
        assert tab.cancel_button.isHidden()
        assert tab.text_edit.toPlainText() == "残る文。"

    def test_cancel_disables_button_and_cancels_worker(self, tab):
        _mine(tab)
        worker = tab.worker_thread
        tab._on_cancel_clicked()
        worker.cancel.assert_called_once()
        assert not tab.cancel_button.isEnabled()
        assert "Cancelling" in tab.cancel_button.text()


class TestCurationContext:
    """Text curation has no media context but wires the definition pane."""

    def test_build_curation_context_wires_lookup_fn(self, tab):
        _mine(tab)
        ctx, lookup_fn = tab._build_curation_context()
        assert ctx is None
        assert lookup_fn is tab.worker_thread.curation_processor.offline_lookup_fn
