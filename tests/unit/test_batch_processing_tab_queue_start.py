"""BatchProcessingTab queue-worker startup wiring (G1 safety net), and the
one-queue-flow merge: the "Add Series" card feeds the same queue the
Multi-Series panel runs, and filling either picker is always read as intent
when Process is pressed.

The manual-pair path used to connect ``finished -> _restore_buttons`` so the
buttons recover once the worker thread ends; the queue path
(``_start_queue_worker``) did not, so a caught run-level failure (stale-dict
gate, AnkiService construction) left the action buttons stranded in the
running state. This asserts the queue path installs the same safety-net
connection -- now the only path there is.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.utils import queue_state_store as store
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab
from anki_miner.gui.widgets.enhanced import ModernButton
from anki_miner.gui.workers.batch_queue_worker import BatchQueueWorkerThread
from anki_miner.models.batch_queue import QueueItemStatus


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
# One queue flow: the Add Series card's pickers feed the queue instead of
# running their own worker, and Process always reads filled pickers as intent.
# ---------------------------------------------------------------------------


def _fill_pickers(tab, tmp_path, name: str = "My Show") -> tuple[Path, Path]:
    video = tmp_path / name
    subs = tmp_path / f"{name} Subs"
    video.mkdir(exist_ok=True)
    subs.mkdir(exist_ok=True)
    tab.video_folder_selector.set_path(str(video))
    tab.subtitle_folder_selector.set_path(str(subs))
    return video, subs


def test_process_queue_with_valid_pickers_and_empty_queue_adds_then_runs(tab, tmp_path, monkeypatch):
    """Muscle memory: fill both pickers, press Process, mine that folder --
    it is added as a series first, then the run proceeds as if the row had
    always been there. Pressing Process also always clears the pickers on a
    successful fold."""
    _fill_pickers(tab, tmp_path)
    started = []
    monkeypatch.setattr(BatchQueueWorkerThread, "start", lambda self: started.append(self))

    tab.queue_panel.process_queue_button.click()

    assert [w.display_name for w in tab.queue_panel.queue_item_widgets] == ["My Show"]
    assert started
    items = tab.worker_thread._requested_items
    assert items is not None
    assert [item.display_name for item in items] == ["My Show"]
    assert tab.video_folder_selector.path_or_none() is None
    assert tab.subtitle_folder_selector.path_or_none() is None


def test_add_to_queue_twice_with_the_same_folders_dedupes_the_name(tab, tmp_path):
    _fill_pickers(tab, tmp_path)
    tab.add_series_button.click()

    assert tab.video_folder_selector.path_or_none() is None
    assert tab.subtitle_folder_selector.path_or_none() is None

    _fill_pickers(tab, tmp_path)  # a successful add clears the pickers
    tab.add_series_button.click()

    assert [w.display_name for w in tab.queue_panel.queue_item_widgets] == ["My Show", "My Show (2)"]


def test_add_to_queue_with_an_invalid_folder_shows_the_issue_and_adds_nothing(tab, tmp_path):
    invalid_video = str(tmp_path / "does-not-exist")
    valid_subtitle = str(tmp_path)
    tab.video_folder_selector.set_path(invalid_video)
    tab.subtitle_folder_selector.set_path(valid_subtitle)

    tab.add_series_button.click()

    assert tab.queue_panel.queue_item_widgets == []
    issue = tab.issue_banner().current_issue()
    assert issue is not None
    assert issue.summary == "Choose existing video and subtitle folders."
    # A failed add leaves the pickers exactly as the user left them.
    assert tab.video_folder_selector.get_path() == invalid_video
    assert tab.subtitle_folder_selector.get_path() == valid_subtitle


def test_process_with_an_error_row_and_new_valid_pickers_adds_and_runs_both(tab, tmp_path):
    """A runnable ERROR row does not block the fold: the new pickers still
    add their own row, and the worker is handed both."""
    old_video, old_subs = _fill_pickers(tab, tmp_path, name="Old Show")
    old_item = tab.queue_panel.add_series(
        display_name="Old Show", video_folder=old_video, subtitle_folder=old_subs, subtitle_offset=0.0
    )
    old_item.status = QueueItemStatus.ERROR
    tab.queue_panel.set_item_status(old_item.id, "error")
    _fill_pickers(tab, tmp_path, name="New Show")

    fake_worker = MagicMock(name="BatchQueueWorkerThread")
    with patch(
        "anki_miner.gui.workers.batch_queue_worker.BatchQueueWorkerThread",
        return_value=fake_worker,
    ) as worker_cls:
        tab.queue_panel.process_queue_button.click()

    assert {w.display_name for w in tab.queue_panel.queue_item_widgets} == {"Old Show", "New Show"}
    items = worker_cls.call_args.kwargs["items"]
    assert {item.display_name for item in items} == {"Old Show", "New Show"}


def test_process_with_pickers_matching_a_pending_row_does_not_duplicate(tab, tmp_path):
    """The same folders already queued mine that row again, not a copy."""
    video, subs = _fill_pickers(tab, tmp_path, name="Show")
    tab.queue_panel.add_series(display_name="Show", video_folder=video, subtitle_folder=subs, subtitle_offset=0.0)
    _fill_pickers(tab, tmp_path, name="Show")  # identical folders again

    fake_worker = MagicMock(name="BatchQueueWorkerThread")
    with patch("anki_miner.gui.workers.batch_queue_worker.BatchQueueWorkerThread", return_value=fake_worker):
        tab.queue_panel.process_queue_button.click()

    assert [w.display_name for w in tab.queue_panel.queue_item_widgets] == ["Show"]


def test_process_with_one_invalid_picker_and_runnable_rows_shows_the_picker_issue(tab, tmp_path):
    """A picker left half-filled is still intent: it refuses with the specific
    issue rather than silently running the rows already queued."""
    video, subs = _fill_pickers(tab, tmp_path, name="Existing Show")
    tab.queue_panel.add_series(
        display_name="Existing Show", video_folder=video, subtitle_folder=subs, subtitle_offset=0.0
    )
    tab.video_folder_selector.set_path(str(tmp_path / "does-not-exist"))
    tab.subtitle_folder_selector.clear()

    with patch("anki_miner.gui.workers.batch_queue_worker.BatchQueueWorkerThread") as worker_cls:
        tab.queue_panel.process_queue_button.click()

    worker_cls.assert_not_called()
    issue = tab.issue_banner().current_issue()
    assert issue is not None
    assert issue.summary == "Choose existing video and subtitle folders."
    assert [w.display_name for w in tab.queue_panel.queue_item_widgets] == ["Existing Show"]


def test_process_with_empty_pickers_and_runnable_rows_just_runs(tab, tmp_path):
    """Both pickers empty is the plain case: run what is already queued."""
    video, subs = _fill_pickers(tab, tmp_path, name="Existing Show")
    tab.queue_panel.add_series(
        display_name="Existing Show", video_folder=video, subtitle_folder=subs, subtitle_offset=0.0
    )
    tab.video_folder_selector.clear()
    tab.subtitle_folder_selector.clear()

    fake_worker = MagicMock(name="BatchQueueWorkerThread")
    with patch(
        "anki_miner.gui.workers.batch_queue_worker.BatchQueueWorkerThread",
        return_value=fake_worker,
    ) as worker_cls:
        tab.queue_panel.process_queue_button.click()

    worker_cls.assert_called_once()
    assert [w.display_name for w in tab.queue_panel.queue_item_widgets] == ["Existing Show"]


@pytest.mark.parametrize(
    ("same_as_subtitle", "expected_summary"),
    [
        (True, "The translation folder must be different from the subtitle folder."),
        (False, "That translation subtitle folder no longer exists."),
    ],
    ids=["equal-to-subtitle", "missing"],
)
def test_a_bad_translation_folder_keeps_its_own_issue_on_an_empty_queue(
    qtbot, test_config, tmp_path, same_as_subtitle, expected_summary
):
    """The translation-folder refusal must survive to the screen: the fold
    must not fall through into the empty-queue's generic refusal."""
    config = replace(test_config, secondary_subtitle_enabled=True)
    secondary_tab = BatchProcessingTab(
        config=config,
        presenter=MagicMock(name="Presenter"),
        progress_callback=MagicMock(name="ProgressCallback"),
    )
    qtbot.addWidget(secondary_tab)
    _video, subs = _fill_pickers(secondary_tab, tmp_path)
    translation = subs if same_as_subtitle else tmp_path / "does-not-exist-translations"
    secondary_tab.secondary_folder_selector.set_path(str(translation))

    secondary_tab.queue_panel.process_queue_button.click()

    issue = secondary_tab.issue_banner().current_issue()
    assert issue is not None
    assert issue.summary == expected_summary
    assert secondary_tab.queue_panel.queue_item_widgets == []
    secondary_tab.deleteLater()


def test_ctrl_shift_a_focuses_the_video_folder_input(tab, qtbot):
    """FileSelector itself is NoFocus with no focus proxy, so the shortcut has
    to reach past it to the line edit it wraps."""
    from PyQt6.QtGui import QKeySequence, QShortcut
    from PyQt6.QtWidgets import QApplication

    shortcut = next(sc for sc in tab.findChildren(QShortcut) if sc.key() == QKeySequence("Ctrl+Shift+A"))
    tab.show()
    qtbot.waitExposed(tab)

    shortcut.activated.emit()

    qtbot.waitUntil(lambda: QApplication.focusWidget() is tab.video_folder_selector.input)


def test_a_pre_change_recovery_snapshot_still_restores(tab, tmp_path, monkeypatch):
    """A queue.batch snapshot written by the pre-merge code, in the exact
    on-disk JSON shape the store still writes today, restores its row with
    its id, offset and status intact -- not just its display name."""
    monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", tmp_path / "home" / "gui_config.json")
    video = tmp_path / "video"
    subtitle = tmp_path / "subs"
    video.mkdir()
    subtitle.mkdir()
    path = store.snapshot_path(tab.QUEUE_STATE_KEY)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "key": "queue.batch",
                "items": [
                    {
                        "id": "pre-change-id",
                        "source": {
                            "kind": "folder_pair",
                            "video": str(video),
                            "subtitle": str(subtitle),
                            "offset": 1.5,
                        },
                        "title": "Old Show",
                        "status": "ready",
                        "retry_count": 0,
                        "error": "",
                        "result_count": 0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    loaded = store.load(tab.QUEUE_STATE_KEY)
    assert loaded is not None
    restored = tab.restore_queue_snapshot(loaded)

    assert restored == 1
    (widget,) = tab.queue_panel.queue_item_widgets
    assert widget.display_name == "Old Show"
    item = tab.queue_panel._items[id(widget)]
    assert item.id == "pre-change-id"
    assert item.subtitle_offset == 1.5
    assert item.status is QueueItemStatus.PENDING


def test_the_screen_has_exactly_one_primary_button(tab):
    primaries = [b for b in tab.findChildren(ModernButton) if b.objectName() == "primary"]

    assert len(primaries) == 1
    assert primaries[0] is tab.queue_panel.process_queue_button
