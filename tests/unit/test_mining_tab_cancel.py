"""Cancel-button flow tests for SingleEpisodeTab and BatchProcessingTab.

Covers ``_on_cancel_clicked`` (release curation dialog + worker.cancel() +
"Cancelling..." button state + status text), ``_restore_buttons`` (hide cancel,
re-show action buttons), and BatchProcessingTab's ``_show_cancel_state``.

The retry-path ``_show_cancel_state`` reveal is already covered in
``test_batch_processing_tab_retry.py``; here we exercise the cancel *click* and
the *restore* halves with a MagicMock worker.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab
from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab


@pytest.fixture
def single_tab(qapp, qtbot, test_config):
    widget = SingleEpisodeTab(
        config=test_config,
        presenter=MagicMock(name="Presenter"),
        progress_callback=MagicMock(name="ProgressCallback"),
    )
    qtbot.addWidget(widget)
    yield widget
    widget.deleteLater()


@pytest.fixture
def batch_tab(qapp, qtbot, test_config):
    widget = BatchProcessingTab(
        config=test_config,
        presenter=MagicMock(name="Presenter"),
        progress_callback=MagicMock(name="ProgressCallback"),
    )
    qtbot.addWidget(widget)
    yield widget
    widget.deleteLater()


# ===========================================================================
# SingleEpisodeTab
# ===========================================================================


def test_single_cancel_click_cancels_worker_and_sets_button_state(single_tab):
    worker = MagicMock(name="EpisodeWorkerThread")
    single_tab.worker_thread = worker

    single_tab._on_cancel_clicked()

    worker.cancel.assert_called_once_with()
    assert single_tab.cancel_button.text() == "Cancelling..."
    assert not single_tab.cancel_button.isEnabled()


def test_single_cancel_click_with_no_worker_is_safe(single_tab):
    """Cancel before a worker exists must not raise; still updates the button."""
    single_tab.worker_thread = None

    single_tab._on_cancel_clicked()  # must not raise

    assert single_tab.cancel_button.text() == "Cancelling..."
    assert not single_tab.cancel_button.isEnabled()


def test_single_cancel_click_sets_status_to_cancelling(single_tab):
    single_tab.worker_thread = MagicMock(name="EpisodeWorkerThread")
    single_tab.progress_widget.set_status = MagicMock()

    single_tab._on_cancel_clicked()

    single_tab.progress_widget.set_status.assert_called_once_with("Cancelling...")


def test_single_cancel_click_releases_active_curation_dialog(single_tab):
    """A cancel must reject any open curation dialog so the worker can resume."""
    single_tab.worker_thread = MagicMock(name="EpisodeWorkerThread")
    dialog = MagicMock(name="WordCurationDialog")
    single_tab._active_curation_dialog = dialog

    single_tab._on_cancel_clicked()

    dialog.reject.assert_called_once_with()
    assert single_tab._curation_cancelled is True


def test_single_restore_buttons_hides_cancel_and_shows_actions(single_tab):
    single_tab.cancel_button.show()
    single_tab.preview_button.hide()
    single_tab.process_button.hide()
    single_tab._is_processing = True

    single_tab._restore_buttons()

    assert single_tab.cancel_button.isHidden()
    assert not single_tab.preview_button.isHidden()
    assert not single_tab.process_button.isHidden()
    assert not single_tab.timing_button.isHidden()
    assert not single_tab.tracks_button.isHidden()
    assert single_tab._is_processing is False


# ===========================================================================
# BatchProcessingTab
# ===========================================================================


def test_batch_cancel_click_cancels_worker_and_sets_button_state(batch_tab):
    worker = MagicMock(name="BatchWorker")
    batch_tab.worker_thread = worker

    batch_tab._on_cancel_clicked()

    worker.cancel.assert_called_once_with()
    assert batch_tab.cancel_button.text() == "Cancelling..."
    assert not batch_tab.cancel_button.isEnabled()


def test_batch_cancel_click_with_no_worker_is_safe(batch_tab):
    batch_tab.worker_thread = None

    batch_tab._on_cancel_clicked()  # must not raise

    assert batch_tab.cancel_button.text() == "Cancelling..."
    assert not batch_tab.cancel_button.isEnabled()


def test_batch_cancel_click_releases_active_curation_dialog(batch_tab):
    batch_tab.worker_thread = MagicMock(name="BatchWorker")
    dialog = MagicMock(name="WordCurationDialog")
    batch_tab._active_curation_dialog = dialog

    batch_tab._on_cancel_clicked()

    dialog.reject.assert_called_once_with()
    assert batch_tab._curation_cancelled is True


def test_batch_cancel_click_sets_current_progress_status(batch_tab):
    batch_tab.worker_thread = MagicMock(name="BatchWorker")
    batch_tab.overall_progress_widget.set_status = MagicMock()

    batch_tab._on_cancel_clicked()

    batch_tab.overall_progress_widget.set_status.assert_called_once_with("Cancelling...")


def test_batch_show_cancel_state_hides_actions_and_reveals_cancel(batch_tab):
    batch_tab.cancel_button.hide()

    batch_tab._show_cancel_state()

    assert batch_tab.process_pairs_button.isHidden()
    assert not batch_tab.cancel_button.isHidden()
    assert batch_tab.cancel_button.isEnabled()
    assert batch_tab.cancel_button.text() == "■ Cancel"


def test_batch_restore_buttons_hides_cancel_and_shows_actions(batch_tab):
    batch_tab._show_cancel_state()
    batch_tab._is_processing = True

    batch_tab._restore_buttons()

    assert batch_tab.cancel_button.isHidden()
    assert not batch_tab.process_pairs_button.isHidden()
    assert batch_tab._is_processing is False


# ---------------------------------------------------------------------------
# Cancel recovery (progress overhaul): the worker suppresses result_ready on a
# cancelled run, so QThread.finished-driven recovery must replace
# "Cancelling..." — it must never strand.
# ---------------------------------------------------------------------------


def test_single_cancel_recovery_shows_cancelled(single_tab):
    single_tab.worker_thread = MagicMock(name="Worker")
    single_tab._on_cancel_clicked()
    assert single_tab.progress_widget.status_label.text() == "Cancelling..."

    # QThread.finished fires even though result_ready was suppressed.
    single_tab._restore_buttons()

    assert single_tab.progress_widget.status_label.text() == "Cancelled"
    assert single_tab.progress_widget.progress_bar.value() == 0


def test_single_run_start_reset_clears_end_state(single_tab):
    """The run-start reset must clear the previous run's pinned summary."""
    single_tab.progress_widget.show_completion("Complete — 87 cards created")
    single_tab.progress_widget.reset()
    single_tab._cancel_requested = False
    assert single_tab.progress_widget.progress_bar.value() == 0
    assert single_tab.progress_widget.status_label.text() == "Ready"
