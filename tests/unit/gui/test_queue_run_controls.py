"""The frozen run, its boundary controls, and the visible retry (D29-A, D30-B).

Pressing Mine locks the queue: nothing on the list can be added, cleared,
removed, retried or reordered until the run ends, and a badge says so. Two
quiet controls appear beside the badge — stop between items, or stop after the
one in flight. Cancel is deliberately not among them: it keeps its single
prompt-free verb next to the run's primary action (D22).

The retry countdown is checked here too, because "visible" is the whole point
of D30-B over D30-A: an automatic attempt nobody can see is indistinguishable
from a stall.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.gui.widgets.audiobook_tab import AudiobookTab
from anki_miner.gui.widgets.youtube_tab import YouTubeTab
from anki_miner.models.youtube import VideoInfo
from anki_miner.models.youtube_queue import YouTubeItemStatus


@pytest.fixture
def audiobook_tab(qtbot, test_config):
    with patch("anki_miner.gui.widgets.audiobook_tab.AudiobookQueueWorker") as worker_cls:
        worker_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")
        widget = AudiobookTab(config=test_config, processor=MagicMock(), presenter=MagicMock())
        qtbot.addWidget(widget)
        yield widget
        widget.deleteLater()


@pytest.fixture
def youtube_tab(qtbot, test_config):
    with patch("anki_miner.gui.widgets.youtube_tab.YouTubeQueueWorker") as worker_cls:
        worker_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")
        widget = YouTubeTab(
            config=test_config,
            processor=MagicMock(),
            fetcher=MagicMock(),
            presenter=MagicMock(),
        )
        qtbot.addWidget(widget)
        yield widget
        widget.deleteLater()


def _add_audiobook(tab, tmp_path: Path, stem: str):
    audio = tmp_path / f"{stem}.m4b"
    sub = tmp_path / f"{stem}.srt"
    audio.touch()
    sub.touch()
    item = tab._queue.add(audio, sub)
    tab._render_new_item(item)
    tab._recompute_buttons()
    return item


def _add_youtube(tab, name: str):
    item = tab._queue.add(f"https://youtu.be/{name}")
    item.status = YouTubeItemStatus.READY
    item.video_info = VideoInfo(
        video_id=name,
        title=name,
        duration_s=120,
        has_manual_ja_subs=True,
        has_auto_ja_subs=False,
        is_live=False,
        is_age_restricted=False,
    )
    item.resolved_sub_mode = "manual_only"
    tab._render_new_item(item)
    tab._recompute_buttons()
    return item


# ---------------------------------------------------------------------------
# The lock
# ---------------------------------------------------------------------------


def test_mine_locks_every_queue_verb(audiobook_tab, tmp_path):
    tab = audiobook_tab
    items = [_add_audiobook(tab, tmp_path, f"v{i}") for i in range(3)]
    tab.list_widget.item(1).setSelected(True)

    tab._on_mine_clicked()

    assert not tab.add_button.isEnabled()
    assert not tab.clear_button.isEnabled()
    assert not tab.mine_button.isEnabled()
    assert not tab.queue_controls.run_button.isEnabled()
    assert not tab.queue_controls.retry_button.isEnabled()
    assert not tab.queue_controls.remove_button.isEnabled()
    assert tab._reorder_locked()
    assert tab.queue_controls.lock_label.text() == "Queue locked while processing."
    # And nothing actually left the queue.
    assert tab._queue.all_items() == items


def test_locked_reorder_leaves_the_model_alone(audiobook_tab, tmp_path):
    tab = audiobook_tab
    first = _add_audiobook(tab, tmp_path, "a")
    second = _add_audiobook(tab, tmp_path, "b")
    tab.list_widget.item(1).setSelected(True)

    tab._on_mine_clicked()
    tab._move_selection(-1)

    assert tab._queue.all_items() == [first, second]


def test_run_end_unlocks_everything(audiobook_tab, tmp_path):
    tab = audiobook_tab
    _add_audiobook(tab, tmp_path, "a")

    tab._on_mine_clicked()
    tab.worker_thread = None
    tab._after_run_cleanup()

    assert tab.add_button.isEnabled()
    assert tab.clear_button.isEnabled()
    assert tab.queue_controls.lock_label.isHidden()
    assert tab.queue_controls.pause_button.isHidden()
    assert tab.queue_controls.finish_button.isHidden()


# ---------------------------------------------------------------------------
# Boundary controls
# ---------------------------------------------------------------------------


def test_pause_asks_the_worker_to_stop_at_the_next_boundary(audiobook_tab, tmp_path):
    tab = audiobook_tab
    _add_audiobook(tab, tmp_path, "a")
    tab._on_mine_clicked()
    worker = tab.worker_thread

    tab.queue_controls.pause_button.click()

    worker.request_pause_after_current.assert_called_once_with()
    # Disabled until the pause actually lands, so it cannot be pressed twice.
    assert not tab.queue_controls.pause_button.isEnabled()


def test_paused_run_reports_where_it_stopped_and_offers_resume(audiobook_tab, tmp_path):
    tab = audiobook_tab
    for i in range(3):
        _add_audiobook(tab, tmp_path, f"v{i}")
    tab._on_mine_clicked()
    tab._items_done = 2

    tab._on_run_paused()

    assert tab.queue_controls.lock_label.text() == "Paused after 2 of 3"
    assert tab.queue_controls.pause_button.text() == "Resume"
    assert tab.queue_controls.pause_button.isEnabled()


def test_resume_continues_the_run(audiobook_tab, tmp_path):
    tab = audiobook_tab
    _add_audiobook(tab, tmp_path, "a")
    tab._on_mine_clicked()
    worker = tab.worker_thread
    tab._on_run_paused()

    tab.queue_controls.pause_button.click()

    worker.resume.assert_called_once_with()
    tab._on_run_resumed()
    assert tab.queue_controls.pause_button.text() == "Pause after current item"
    assert tab.queue_controls.lock_label.text() == "Queue locked while processing."


def test_finish_current_then_stop_is_a_separate_quiet_control(audiobook_tab, tmp_path):
    tab = audiobook_tab
    _add_audiobook(tab, tmp_path, "a")
    tab._on_mine_clicked()
    worker = tab.worker_thread

    tab.queue_controls.finish_button.click()

    worker.request_stop_after_current.assert_called_once_with()
    worker.cancel.assert_not_called()
    assert not tab.queue_controls.finish_button.isEnabled()


def test_cancel_still_takes_no_prompt(audiobook_tab, tmp_path, monkeypatch):
    """D22: one verb, straight to a disabled 'Cancelling…'."""
    from PyQt6.QtWidgets import QMessageBox

    tab = audiobook_tab
    _add_audiobook(tab, tmp_path, "a")
    tab._on_mine_clicked()
    worker = tab.worker_thread

    def _no_dialogs(*_args, **_kwargs):
        raise AssertionError("Cancel must not raise a confirmation dialog")

    for name in ("question", "warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, name, _no_dialogs)

    tab._on_stop_all_clicked()

    worker.cancel.assert_called_once_with()
    assert tab.stop_button.text() == "Cancelling…"
    assert not tab.stop_button.isEnabled()


# ---------------------------------------------------------------------------
# Visible retry
# ---------------------------------------------------------------------------


def test_retry_countdown_reaches_the_status_line_and_the_task_snapshot(youtube_tab):
    tab = youtube_tab
    _add_youtube(tab, "a")
    tab._on_mine_clicked()
    tab._on_item_started(0)

    tab._on_item_retrying(0, 2, 3, 8)

    assert "Attempt 2 of 3 · retrying in 8s" in tab.progress_widget.status_label.text()


def test_retry_countdown_logs_once_per_attempt(youtube_tab):
    tab = youtube_tab
    _add_youtube(tab, "a")
    tab._on_mine_clicked()
    tab._on_item_started(0)

    for remaining in (8, 7, 6):
        tab._on_item_retrying(0, 2, 3, remaining)
    tab._on_item_retrying(0, 3, 3, 8)

    logged = tab.log_widget.text_edit.toPlainText()
    assert logged.count("Attempt 2 of 3") == 1
    assert logged.count("Attempt 3 of 3") == 1


def test_reading_tabs_report_the_retry_too(qtbot, test_config):
    """The base logs the countdown wherever a tab supplies the template."""
    from anki_miner.gui.widgets.reading_novels_tab import ReadingNovelsTab

    with patch("anki_miner.gui.widgets._reading_mining_base.ReadingQueueWorker"):
        tab = ReadingNovelsTab(config=test_config, processor=MagicMock(), presenter=MagicMock())
        qtbot.addWidget(tab)
        tab._on_item_retrying(0, 2, 3, 8)
        assert "Attempt 2 of 3 · retrying in 8s" in tab.log_widget.text_edit.toPlainText()
        tab.deleteLater()
