"""BatchProcessingTab queue-worker startup wiring (G1 safety net), and the
one-queue-flow merge (T16): the "Add Series" card feeds the same queue the
Multi-Series panel runs, so pressing Process on an empty queue with valid
pickers adds the series and then runs it.

The manual-pair path used to connect ``finished -> _restore_buttons`` so the
buttons recover once the worker thread ends; the queue path
(``_start_queue_worker``) did not, so a caught run-level failure (stale-dict
gate, AnkiService construction) left the action buttons stranded in the
running state. This asserts the queue path installs the same safety-net
connection -- now the only path there is.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.utils import queue_state_store as store
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab
from anki_miner.gui.widgets.enhanced import ModernButton
from anki_miner.gui.workers.batch_queue_worker import BatchQueueWorkerThread


@pytest.fixture
def tab(qapp, qtbot, test_config):
    widget = BatchProcessingTab(
        config=test_config,
        presenter=MagicMock(name="Presenter"),
        progress_callback=MagicMock(name="ProgressCallback"),
    )
    qtbot.addWidget(widget)
    yield widget
    widget.deleteLater()


def test_start_queue_worker_connects_finished_to_restore_buttons(tab):
    """The queue path wires ``finished -> _restore_buttons`` (like the quick path)."""
    tab.worker_thread = None  # so _teardown_previous_run is a no-op
    fake_worker = MagicMock(name="BatchQueueWorkerThread")

    with patch(
        "anki_miner.gui.workers.batch_queue_worker.BatchQueueWorkerThread",
        return_value=fake_worker,
    ):
        tab._start_queue_worker()

    fake_worker.finished.connect.assert_any_call(tab._restore_buttons)
    fake_worker.start.assert_called_once()


def test_start_queue_worker_connects_item_pairs_progress(tab):
    """The queue path wires the within-series episode ticks into the Overall bar."""
    tab.worker_thread = None
    fake_worker = MagicMock(name="BatchQueueWorkerThread")

    with patch(
        "anki_miner.gui.workers.batch_queue_worker.BatchQueueWorkerThread",
        return_value=fake_worker,
    ):
        tab._start_queue_worker()

    fake_worker.item_pairs_progress.connect.assert_any_call(tab._on_item_pairs_progress)


def test_an_all_complete_queue_is_told_how_to_run_again(tab):
    """The dead end names the way out instead of just refusing."""
    tab.queue_panel.has_only_completed_rows = lambda: True

    summary = tab._empty_run_summary()

    assert "Run selected" in summary
    assert "already complete" in summary


def test_an_unrunnable_queue_still_reports_the_plain_reason(tab):
    tab.queue_panel.has_only_completed_rows = lambda: False

    assert tab._empty_run_summary() == "No valid series in the queue to process."


# ---------------------------------------------------------------------------
# One queue flow (T16): the quick pickers add a series instead of running
# their own worker.
# ---------------------------------------------------------------------------


def _fill_pickers(tab, tmp_path, name: str = "My Show") -> None:
    video = tmp_path / name
    subs = tmp_path / f"{name} Subs"
    video.mkdir(exist_ok=True)
    subs.mkdir(exist_ok=True)
    tab.video_folder_selector.set_path(str(video))
    tab.subtitle_folder_selector.set_path(str(subs))


def test_process_queue_with_valid_pickers_and_empty_queue_adds_then_runs(tab, tmp_path, monkeypatch):
    """Review Focus 4a: muscle memory -- fill both pickers, press Process, mine it."""
    _fill_pickers(tab, tmp_path)
    started = []
    monkeypatch.setattr(BatchQueueWorkerThread, "start", lambda self: started.append(self))

    tab.queue_panel.process_queue_button.click()

    assert [w.display_name for w in tab.queue_panel.queue_item_widgets] == ["My Show"]
    assert started


def test_add_to_queue_twice_with_the_same_folders_dedupes_the_name(tab, tmp_path):
    _fill_pickers(tab, tmp_path)
    tab.add_series_button.click()
    _fill_pickers(tab, tmp_path)  # a successful add clears the pickers
    tab.add_series_button.click()

    assert [w.display_name for w in tab.queue_panel.queue_item_widgets] == ["My Show", "My Show (2)"]


def test_add_to_queue_with_an_invalid_folder_shows_the_issue_and_adds_nothing(tab, tmp_path):
    tab.video_folder_selector.set_path(str(tmp_path / "does-not-exist"))
    tab.subtitle_folder_selector.set_path(str(tmp_path))

    tab.add_series_button.click()

    assert tab.queue_panel.queue_item_widgets == []
    issue = tab.issue_banner().current_issue()
    assert issue is not None
    assert issue.summary == "Choose existing video and subtitle folders."


def test_a_pre_change_recovery_snapshot_still_restores(tab, tmp_path, monkeypatch):
    """Review Focus 4b: a queue.batch snapshot from before this merge, built
    from the store's own literal format (not this tab's writer), still
    restores its rows."""
    monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", tmp_path / "home" / "gui_config.json")
    video = tmp_path / "video"
    subtitle = tmp_path / "subs"
    video.mkdir()
    subtitle.mkdir()
    snapshot = store.QueueSnapshot(
        key=tab.QUEUE_STATE_KEY,
        items=(
            store.QueueItemSnapshot(
                item_id="pre-change-id",
                source=store.folder_pair_source(video, subtitle, offset=1.5),
                title="Old Show",
                status=store.STATUS_READY,
                retry_count=0,
                error="",
                result_count=0,
            ),
        ),
    )
    store.save(snapshot)

    loaded = store.load(tab.QUEUE_STATE_KEY)
    assert loaded is not None
    restored = tab.restore_queue_snapshot(loaded)

    assert restored == 1
    assert [w.display_name for w in tab.queue_panel.queue_item_widgets] == ["Old Show"]


def test_the_screen_has_exactly_one_primary_button(tab):
    primaries = [b for b in tab.findChildren(ModernButton) if b.objectName() == "primary"]

    assert len(primaries) == 1
    assert primaries[0] is tab.queue_panel.process_queue_button
