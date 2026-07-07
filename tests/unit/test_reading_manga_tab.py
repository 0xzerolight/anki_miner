"""Tests for the manga sub-tab of the Reading tab.

``ReadingMangaTab`` pairs a quick-folder Preview/Mine card with a volume queue
over the shared ``_ReadingMiningTabBase`` lifecycle. Behaviour under test:

* Add: each accepted path is classified by ``detect`` into one or more READY
  rows; a ``SetupError`` is surfaced in the log and adds no row.
* Quick run: a single-volume folder mines an ephemeral item that never enters
  ``self._queue``; a series folder of >1 volume fills the queue and does NOT
  start. Both dialogs feed ``_add_source_path``.
* Queue run: ``Process Queue`` mines every READY item (mine-only).
* Buttons: pure derived state — quick Preview/Mine give way to Cancel while a
  run is active; Adds + Process Queue disabled during a run; Clear trims the
  tail mid-run.
* Per-item signals are READ-ONLY on item state (the worker owns the lifecycle):
  they refresh the row + dual progress bars but never write status/cards.
* Drag-drop queues manga sources; a dropped novel earns a cross-tab hint, no
  row.
* Cancel (shared quick/queue) releases any curation dialog then cancels.
* D8: ``_build_curation_context`` inherits the base ``(None, None)`` even with a
  live worker — reading curation is table-only.

Qt threads are never started — ``ReadingQueueWorker`` is class-level patched at
the base module so ``start()`` is a no-op and constructor kwargs can be
inspected.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import SetupError
from anki_miner.gui.widgets.reading_manga_tab import ReadingMangaTab
from anki_miner.models.reading_queue import ReadingItemStatus
from anki_miner.services.reading.models import ReadingSourceRef

_WORKER_TARGET = "anki_miner.gui.widgets._reading_mining_base.ReadingQueueWorker"
_DETECT = "anki_miner.gui.widgets.reading_manga_tab.detector.detect"
_OPEN_FILES = "anki_miner.gui.widgets.reading_manga_tab.QFileDialog.getOpenFileNames"
_GET_DIR = "anki_miner.gui.widgets.reading_manga_tab.QFileDialog.getExistingDirectory"
_URLS = "anki_miner.gui.widgets.reading_manga_tab.urls_from_event"


@pytest.fixture
def tab(qtbot, test_config: AnkiMinerConfig):
    """Instantiate a ReadingMangaTab with the queue worker class patched.

    ``ReadingQueueWorker`` is patched at the base module where ``_launch_run``
    looks it up, so ``start()`` doesn't spawn a real QThread and constructor
    kwargs can be inspected via ``tab._queue_worker_cls``.
    """
    with patch(_WORKER_TARGET, autospec=False) as queue_cls:
        queue_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")

        widget = ReadingMangaTab(
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


def _make_ref(kind: str = "mokuro", title: str = "Series Vol.1") -> ReadingSourceRef:
    """Build a ReadingSourceRef for a single volume/book."""
    ext = {"mokuro": ".mokuro", "epub": ".epub", "txt": ".txt"}[kind]
    return ReadingSourceRef(
        kind=kind,  # type: ignore[arg-type]
        path=Path(f"/src/{title}{ext}"),
        image_root=None,
        title=title,
        volume="1" if kind == "mokuro" else None,
    )


def _add_refs(tab, refs, path: str = "/src/whatever"):
    """Patch ``detect`` to return *refs* and drive ``_add_source_path``.

    Returns the queue items created by the add (the tail ``len(refs)`` items).
    """
    with patch(_DETECT, return_value=list(refs)):
        tab._add_source_path(Path(path))
    n = len(refs)
    return tab._queue.all_items()[-n:] if n else []


def _add_one(tab, kind: str = "mokuro", title: str = "Series Vol.1"):
    """Add a single source ref and return its queue item."""
    return _add_refs(tab, [_make_ref(kind, title)])[0]


def _url(local_path: str):
    """A fake QUrl whose toLocalFile returns *local_path*."""
    u = MagicMock()
    u.toLocalFile.return_value = local_path
    return u


def _quick_run(tab, folder: Path, refs, *, preview: bool = False):
    """Select a valid *folder*, patch ``detect`` to return *refs*, click Preview/Mine.

    *folder* must exist on disk so the folder-mode selector validates.
    """
    tab.volume_folder_selector.set_path(str(folder))
    assert tab.volume_folder_selector.is_valid()
    with patch(_DETECT, return_value=list(refs)):
        if preview:
            tab._on_preview_clicked()
        else:
            tab._on_mine_clicked()


class TestInitialState:
    """Empty queue: quick buttons visible, queue actions disabled."""

    def test_empty_state_buttons(self, tab):
        assert tab._queue.all_items() == []
        assert tab.add_series_button.isEnabled()
        assert tab.add_volumes_button.isEnabled()
        assert not tab.preview_button.isHidden()
        assert not tab.mine_button.isHidden()
        assert tab.cancel_button.isHidden()
        assert not tab.process_queue_button.isEnabled()
        assert not tab.clear_button.isEnabled()
        assert tab.worker_thread is None

    def test_list_widget_empty(self, tab):
        assert tab.list_widget.count() == 0
        assert not tab.empty_label.isHidden()

    def test_review_checkbox_default_unchecked(self, tab):
        assert tab.review_words_checkbox.isChecked() is False


class TestAddSource:
    """Add classifies each path via detect and queues READY item(s)."""

    def test_add_single_ref_creates_ready_item(self, tab):
        item = _add_one(tab, "mokuro", "My Manga Vol.1")

        items = tab._queue.all_items()
        assert len(items) == 1
        assert items[0] is item
        assert item.title == "My Manga Vol.1"
        assert item.kind == "mokuro"
        assert item.status == ReadingItemStatus.READY

    def test_add_renders_row(self, tab):
        item = _add_one(tab)
        assert tab.list_widget.count() == 1
        assert item in tab._row_widgets

    def test_add_series_dir_expands_to_n_rows(self, tab):
        refs = [_make_ref("mokuro", f"Series Vol.{i}") for i in range(1, 4)]
        items = _add_refs(tab, refs, path="/src/Series")

        assert len(items) == 3
        assert tab.list_widget.count() == 3
        assert [i.title for i in items] == ["Series Vol.1", "Series Vol.2", "Series Vol.3"]

    def test_add_enables_queue_buttons(self, tab):
        _add_one(tab)
        assert tab.process_queue_button.isEnabled()
        assert tab.clear_button.isEnabled()

    def test_detect_error_surfaced_no_row(self, tab):
        with patch(_DETECT, side_effect=SetupError("No .mokuro sidecar found for 'x.cbz'.")):
            tab._add_source_path(Path("/src/x.cbz"))

        assert tab._queue.all_items() == []
        assert tab.list_widget.count() == 0
        assert "sidecar" in tab.log_widget.text_edit.toPlainText().lower()

    def test_unexpected_detect_error_surfaced_no_row(self, tab):
        with patch(_DETECT, side_effect=RuntimeError("boom")):
            tab._add_source_path(Path("/src/weird"))

        assert tab._queue.all_items() == []
        assert "boom" in tab.log_widget.text_edit.toPlainText()


class TestAddButtons:
    """Add Series Folder / Add Volumes open dialogs and feed detect."""

    def test_add_series_queues_folder_volumes(self, tab):
        refs = [_make_ref("mokuro", f"Vol.{i}") for i in range(1, 3)]
        with (
            patch(_GET_DIR, return_value="/src/Series"),
            patch(_DETECT, return_value=refs),
        ):
            tab._on_add_series_clicked()

        assert [i.title for i in tab._queue.all_items()] == ["Vol.1", "Vol.2"]

    def test_add_series_cancelled_dialog_noop(self, tab):
        with patch(_GET_DIR, return_value=""):
            tab._on_add_series_clicked()
        assert tab._queue.all_items() == []

    def test_add_volumes_queues_picked_files(self, tab):
        with (
            patch(_OPEN_FILES, return_value=(["/src/a.mokuro", "/src/b.mokuro"], "")),
            patch(_DETECT, side_effect=lambda p: [_make_ref("mokuro", p.stem)]),
        ):
            tab._on_add_volumes_clicked()

        assert [i.title for i in tab._queue.all_items()] == ["a", "b"]

    def test_add_volumes_cancelled_dialog_noop(self, tab):
        with patch(_OPEN_FILES, return_value=([], "")):
            tab._on_add_volumes_clicked()
        assert tab._queue.all_items() == []

    def test_add_disabled_during_run_is_noop(self, tab):
        _add_one(tab, "mokuro", "a")
        tab._on_process_queue_clicked()
        assert not tab.add_volumes_button.isEnabled()

        with patch(_OPEN_FILES) as dlg:
            tab._on_add_volumes_clicked()
            dlg.assert_not_called()  # guarded out before opening the picker

        with patch(_GET_DIR) as dlg2:
            tab._on_add_series_clicked()
            dlg2.assert_not_called()


class TestDragDrop:
    """Dropped folders/manga route through _add_source_path; novels earn a hint."""

    def test_drop_manga_file_adds_row(self, tab):
        event = MagicMock()
        with (
            patch(_URLS, return_value=[_url("/src/a.mokuro")]),
            patch(_DETECT, return_value=[_make_ref("mokuro", "a")]),
        ):
            tab.dropEvent(event)

        assert [i.title for i in tab._queue.all_items()] == ["a"]
        event.acceptProposedAction.assert_called_once()

    def test_drop_folder_adds_rows(self, tmp_path, tab):
        event = MagicMock()
        with (
            patch(_URLS, return_value=[_url(str(tmp_path))]),
            patch(_DETECT, return_value=[_make_ref("mokuro", "Vol.1")]),
        ):
            tab.dropEvent(event)

        assert [i.title for i in tab._queue.all_items()] == ["Vol.1"]

    def test_drop_novel_hints_no_row(self, tab):
        event = MagicMock()
        with (
            patch(_URLS, return_value=[_url("/src/book.epub")]),
            patch(_DETECT) as detect,
        ):
            tab.dropEvent(event)

        detect.assert_not_called()
        assert tab._queue.all_items() == []
        assert "novels" in tab.log_widget.text_edit.toPlainText().lower()
        event.acceptProposedAction.assert_called_once()

    def test_drop_none_event_is_noop(self, tab):
        tab.dropEvent(None)  # must not raise
        assert tab._queue.all_items() == []

    def test_drag_enter_accepts_manga_and_novel(self, tab):
        for name in ("/src/a.mokuro", "/src/a.cbz", "/src/a.epub"):
            event = MagicMock()
            with patch(_URLS, return_value=[_url(name)]):
                tab.dragEnterEvent(event)
            event.acceptProposedAction.assert_called_once()


class TestQuickRun:
    """Quick-card Preview/Mine: one volume mines ephemerally; a series fills the queue."""

    def test_single_volume_mines_ephemeral_no_row(self, tmp_path, tab):
        queue_cls = tab._queue_worker_cls
        _quick_run(tab, tmp_path, [_make_ref("mokuro", "Solo Vol.1")])

        assert queue_cls.call_count == 1
        items = queue_cls.call_args.kwargs["items"]
        assert [i.title for i in items] == ["Solo Vol.1"]
        # Ephemeral: never added to the queue, no row rendered.
        assert tab._queue.all_items() == []
        assert tab.list_widget.count() == 0
        assert items[0] not in tab._queue.all_items()
        assert tab.worker_thread is not None
        assert queue_cls.call_args.kwargs["preview_mode"] is False
        tab.worker_thread.start.assert_called_once()

    def test_single_volume_preview_flag(self, tmp_path, tab):
        queue_cls = tab._queue_worker_cls
        _quick_run(tab, tmp_path, [_make_ref("mokuro", "Solo Vol.1")], preview=True)

        assert queue_cls.call_args.kwargs["preview_mode"] is True

    def test_series_folder_fills_queue_without_starting(self, tmp_path, tab):
        queue_cls = tab._queue_worker_cls
        refs = [_make_ref("mokuro", f"Vol.{i}") for i in range(1, 4)]
        _quick_run(tab, tmp_path, refs)

        assert queue_cls.call_count == 0
        assert tab.worker_thread is None
        assert len(tab._queue.all_items()) == 3
        assert tab.list_widget.count() == 3
        assert "3 volumes" in tab.log_widget.text_edit.toPlainText()

    def test_launch_success_seeds_overall_bar(self, tmp_path, tab):
        _quick_run(tab, tmp_path, [_make_ref("mokuro", "Solo")])
        assert "Starting" in tab.overall_progress_widget.status_label.text()

    def test_empty_folder_warns_no_run(self, tab):
        queue_cls = tab._queue_worker_cls
        tab._on_mine_clicked()
        assert queue_cls.call_count == 0
        assert tab.worker_thread is None
        assert "valid volume folder" in tab.log_widget.text_edit.toPlainText().lower()

    def test_invalid_folder_warns_no_run(self, tab):
        queue_cls = tab._queue_worker_cls
        tab.volume_folder_selector.set_path("/no/such/folder")
        tab._on_mine_clicked()
        assert queue_cls.call_count == 0
        assert "valid volume folder" in tab.log_widget.text_edit.toPlainText().lower()

    def test_detect_error_surfaced_no_run(self, tmp_path, tab):
        queue_cls = tab._queue_worker_cls
        tab.volume_folder_selector.set_path(str(tmp_path))
        with patch(_DETECT, side_effect=SetupError("not a recognized reading source")):
            tab._on_mine_clicked()
        assert queue_cls.call_count == 0
        assert "recognized reading source" in tab.log_widget.text_edit.toPlainText()

    def test_quick_run_refused_while_worker_active(self, tmp_path, tab):
        _add_one(tab)
        tab._on_process_queue_clicked()
        queue_cls = tab._queue_worker_cls
        calls_before = queue_cls.call_count

        _quick_run(tab, tmp_path, [_make_ref("mokuro", "Solo")])
        assert queue_cls.call_count == calls_before  # no second worker


class TestProcessQueue:
    """Process Queue mines every READY item, mine-only."""

    def test_process_queue_constructs_worker_preview_false(self, tab):
        _add_one(tab)
        queue_cls = tab._queue_worker_cls
        tab._on_process_queue_clicked()

        assert queue_cls.call_count == 1
        kwargs = queue_cls.call_args.kwargs
        assert kwargs["preview_mode"] is False
        assert kwargs["processor"] is tab._processor
        assert kwargs["config"] is tab._config
        assert kwargs["curation_callback"] is None
        assert tab.worker_thread is not None
        tab.worker_thread.start.assert_called_once()

    def test_process_queue_wires_signals(self, tab):
        _add_one(tab)
        tab._on_process_queue_clicked()
        worker = tab.worker_thread

        worker.item_started.connect.assert_called_once_with(tab._on_item_started)
        worker.item_progress.connect.assert_called_once_with(tab._on_item_progress)
        worker.item_finished.connect.assert_called_once_with(tab._on_item_finished)
        worker.queue_finished.connect.assert_called_once_with(tab._on_queue_finished)
        worker.finished.connect.assert_called_once_with(tab._on_worker_finished)

    def test_process_queue_passes_ready_items_only(self, tab):
        done = _add_one(tab, "mokuro", "done")
        ready = _add_one(tab, "mokuro", "ready")
        done.status = ReadingItemStatus.COMPLETED

        queue_cls = tab._queue_worker_cls
        tab._on_process_queue_clicked()

        assert queue_cls.call_args.kwargs["items"] == [ready]

    def test_process_queue_no_ready_noop(self, tab):
        queue_cls = tab._queue_worker_cls
        tab._on_process_queue_clicked()
        assert queue_cls.call_count == 0
        assert tab.worker_thread is None

    def test_process_queue_callback_follows_checkbox(self, tab):
        queue_cls = tab._queue_worker_cls
        _add_one(tab)
        tab.review_words_checkbox.setChecked(True)
        tab._on_process_queue_clicked()
        assert queue_cls.call_args.kwargs["curation_callback"] == tab._curation_bridge


class TestButtonMatrix:
    """Pure derived button state: running vs idle."""

    def test_idle_with_ready_item(self, tab):
        _add_one(tab)
        assert not tab.preview_button.isHidden()
        assert not tab.mine_button.isHidden()
        assert tab.cancel_button.isHidden()
        assert tab.add_series_button.isEnabled()
        assert tab.add_volumes_button.isEnabled()
        assert tab.process_queue_button.isEnabled()
        assert tab.clear_button.isEnabled()

    def test_running_queue_hides_quick_shows_cancel(self, tab):
        _add_one(tab)
        tab._on_process_queue_clicked()

        assert tab.preview_button.isHidden()
        assert tab.mine_button.isHidden()
        assert not tab.cancel_button.isHidden()
        assert not tab.add_series_button.isEnabled()
        assert not tab.add_volumes_button.isEnabled()
        assert not tab.process_queue_button.isEnabled()
        assert tab.clear_button.isEnabled()  # has queued items

    def test_running_quick_hides_quick_buttons(self, tmp_path, tab):
        _quick_run(tab, tmp_path, [_make_ref("mokuro", "Solo")])

        assert tab.preview_button.isHidden()
        assert tab.mine_button.isHidden()
        assert not tab.cancel_button.isHidden()
        # Ephemeral quick run leaves the queue empty → nothing to clear.
        assert not tab.clear_button.isEnabled()


class TestPerItemSignalsReadOnly:
    """Per-item signals refresh the UI but never write item state."""

    def test_item_started_sets_current_bar_no_status_write(self, tab):
        item_a = _add_one(tab, "mokuro", "vol1")
        _add_one(tab, "mokuro", "vol2")
        _add_one(tab, "mokuro", "vol3")
        tab._on_process_queue_clicked()

        # Worker owns lifecycle: emulate it having already completed item_a.
        item_a.status = ReadingItemStatus.COMPLETED
        item_a.cards_created = 7

        tab._on_item_started(0)

        assert item_a.status == ReadingItemStatus.COMPLETED
        assert item_a.cards_created == 7
        text = tab.current_progress_widget.status_label.text()
        assert "Mining 1 of 3" in text
        assert "vol1" in text

    def test_item_started_refreshes_row_from_worker_status(self, tab):
        item = _add_one(tab, "mokuro", "vol1")
        tab._on_process_queue_clicked()
        item.status = ReadingItemStatus.PROCESSING

        tab._on_item_started(0)

        assert not tab._row_widgets[item].remove_button.isEnabled()

    def test_item_progress_determinate(self, tab):
        _add_one(tab)
        tab._on_process_queue_clicked()
        tab._on_item_started(0)

        tab._on_item_progress(0, "Loading pages", 42)

        assert tab.current_progress_widget.progress_bar.maximum() == 100
        assert tab.current_progress_widget.progress_bar.value() == 42
        assert "Loading pages" in tab.current_progress_widget.status_label.text()

    def test_item_progress_indeterminate(self, tab):
        _add_one(tab)
        tab._on_process_queue_clicked()
        tab._on_item_started(0)

        tab._on_item_progress(0, "Fetching definitions", -1)

        assert tab.current_progress_widget.progress_bar.maximum() == 0
        assert "Fetching definitions" in tab.current_progress_widget.status_label.text()

    def test_item_finished_success_reads_worker_state(self, tab):
        item = _add_one(tab, "mokuro", "vol1")
        tab._on_process_queue_clicked()
        tab._on_item_started(0)

        item.status = ReadingItemStatus.COMPLETED
        item.cards_created = 5
        result = MagicMock(cards_created=5)

        tab._on_item_finished(0, result, None, 1)

        assert "5 cards created" in tab._row_widgets[item].detail_label.full_text
        assert "5 cards" in tab.log_widget.text_edit.toPlainText()
        tab._presenter.show_processing_result.assert_called_once_with(result)

    def test_item_finished_does_not_write_state(self, tab):
        item = _add_one(tab)
        tab._on_process_queue_clicked()
        tab._on_item_started(0)
        before_status = item.status
        before_cards = item.cards_created

        tab._on_item_finished(0, MagicMock(cards_created=99), None, 1)

        assert item.status == before_status
        assert item.cards_created == before_cards

    def test_item_finished_error_logged(self, tab):
        item = _add_one(tab, "mokuro", "vol1")
        tab._on_process_queue_clicked()
        tab._on_item_started(0)
        item.status = ReadingItemStatus.ERROR
        item.error_message = "SetupError: DRM"

        tab._on_item_finished(0, None, "SetupError: DRM", 1)

        assert "SetupError: DRM" in tab.log_widget.text_edit.toPlainText()
        assert "SetupError: DRM" in tab._row_widgets[item].detail_label.full_text

    def test_item_finished_presenter_error_swallowed(self, tab):
        item = _add_one(tab)
        tab._on_process_queue_clicked()
        tab._on_item_started(0)
        item.status = ReadingItemStatus.COMPLETED
        tab._presenter.show_processing_result.side_effect = RuntimeError("presenter blew up")

        tab._on_item_finished(0, MagicMock(cards_created=1), None, 1)  # must not raise

    def test_item_started_out_of_range_idx_is_noop(self, tab):
        item = _add_one(tab)
        tab._on_process_queue_clicked()
        status_before = tab.current_progress_widget.status_label.text()

        tab._on_item_started(99)

        assert item.status == ReadingItemStatus.READY
        assert tab.current_progress_widget.status_label.text() == status_before

    def test_item_finished_out_of_range_idx_is_noop(self, tab):
        _add_one(tab)
        tab._on_process_queue_clicked()
        tab._on_item_started(0)

        tab._on_item_finished(99, None, "err", 1)

        tab._presenter.show_processing_result.assert_not_called()


class TestOverallBar:
    """The overall bar advances over terminal items in the run snapshot."""

    def test_overall_bar_advances_on_finish(self, tab):
        first = _add_one(tab, "mokuro", "vol1")
        _add_one(tab, "mokuro", "vol2")
        tab._on_process_queue_clicked()
        tab._on_item_started(0)

        first.status = ReadingItemStatus.COMPLETED
        tab._on_item_finished(0, MagicMock(cards_created=1), None, 1)

        assert "Completed: 1/2" in tab.overall_progress_widget.status_label.text()
        assert tab.overall_progress_widget.progress_bar.value() == 50


class TestQueueFinished:
    """``queue_finished`` logs a run summary over ``_run_items``."""

    def test_queue_finished_summary_over_run_items(self, tab):
        good = _add_one(tab, "mokuro", "good")
        bad = _add_one(tab, "mokuro", "bad")
        tab._on_process_queue_clicked()
        good.status = ReadingItemStatus.COMPLETED
        bad.status = ReadingItemStatus.ERROR

        tab._on_queue_finished()

        text = tab.log_widget.text_edit.toPlainText()
        assert "1 succeeded" in text
        assert "1 failed" in text

    def test_queue_finished_covers_ephemeral_quick_item(self, tmp_path, tab):
        _quick_run(tab, tmp_path, [_make_ref("mokuro", "Solo")])
        ephemeral = tab._run_items[0]
        ephemeral.status = ReadingItemStatus.COMPLETED

        tab._on_queue_finished()

        assert "1 succeeded, 0 failed" in tab.log_widget.text_edit.toPlainText()

    def test_queue_finished_does_not_mutate_state(self, tab):
        _add_one(tab)
        tab._on_process_queue_clicked()
        worker = tab.worker_thread

        tab._on_queue_finished()

        assert tab.worker_thread is worker
        assert tab._run_items != []


class TestAfterRunCleanup:
    """The base cleanup slot restores the Cancel button and both bars."""

    def test_cleanup_restores_cancel_and_bars(self, tab):
        _add_one(tab)
        tab._on_process_queue_clicked()
        tab._on_item_started(0)
        tab._on_item_progress(0, "Loading", -1)
        tab._on_cancel_clicked()
        assert tab.cancel_button.text() == "Cancelling…"

        tab._on_worker_finished()

        assert tab.cancel_button.text() == "Cancel"
        assert tab.cancel_button.isEnabled()
        assert tab.overall_progress_widget.progress_bar.maximum() == 100
        assert tab.current_progress_widget.progress_bar.maximum() == 100
        assert tab.current_progress_widget.status_label.text() == "Ready"
        assert tab.worker_thread is None
        assert tab._run_items == []
        # Idle again: quick buttons restored, Cancel hidden.
        assert not tab.preview_button.isHidden()
        assert tab.cancel_button.isHidden()


class TestCancel:
    """Cancel forwards to worker.cancel() and releases any curation dialog."""

    def test_cancel_during_queue_run(self, tab):
        _add_one(tab)
        tab._on_process_queue_clicked()
        worker = tab.worker_thread

        tab._on_cancel_clicked()

        worker.cancel.assert_called_once()  # type: ignore[union-attr]
        assert not tab.cancel_button.isEnabled()
        assert tab.cancel_button.text() == "Cancelling…"

    def test_cancel_during_quick_run(self, tmp_path, tab):
        _quick_run(tab, tmp_path, [_make_ref("mokuro", "Solo")])
        worker = tab.worker_thread

        tab._on_cancel_clicked()

        worker.cancel.assert_called_once()  # type: ignore[union-attr]
        assert tab.cancel_button.text() == "Cancelling…"

    def test_cancel_releases_active_curation_dialog(self, tab):
        _add_one(tab)
        tab._on_process_queue_clicked()
        with patch.object(tab, "_cancel_active_curation_dialog") as cancel:
            tab._on_cancel_clicked()
            cancel.assert_called_once()

    def test_cancel_noop_when_no_worker(self, tab):
        tab._on_cancel_clicked()  # must not raise


class TestRemoveAndClear:
    """Remove button and Clear All manage queue contents."""

    def test_remove_item(self, tab):
        item = _add_one(tab, "mokuro", "vol1")
        keep = _add_one(tab, "mokuro", "vol2")

        tab._on_remove_clicked(item)

        assert tab._queue.all_items() == [keep]
        assert tab.list_widget.count() == 1
        assert item not in tab._row_widgets

    def test_remove_processing_item_is_noop(self, tab):
        item = _add_one(tab)
        tab._on_process_queue_clicked()
        item.status = ReadingItemStatus.PROCESSING

        tab._on_remove_clicked(item)

        assert tab._queue.all_items() == [item]

    def test_remove_during_run_skips_item_in_worker(self, tab):
        _add_one(tab, "mokuro", "vol1")
        item2 = _add_one(tab, "mokuro", "vol2")
        tab._on_process_queue_clicked()
        worker = tab.worker_thread

        tab._on_remove_clicked(item2)

        worker.skip_item.assert_called_once_with(item2)
        assert item2 not in tab._queue.all_items()

    def test_clear_removes_non_processing(self, tab):
        _add_one(tab, "mokuro", "vol1")
        _add_one(tab, "mokuro", "vol2")

        tab._on_clear_clicked()

        assert tab._queue.all_items() == []
        assert tab.list_widget.count() == 0
        assert not tab.clear_button.isEnabled()

    def test_clear_during_run_preserves_processing(self, tab):
        item1 = _add_one(tab, "mokuro", "vol1")
        item2 = _add_one(tab, "mokuro", "vol2")
        item3 = _add_one(tab, "mokuro", "vol3")
        tab._on_process_queue_clicked()
        item1.status = ReadingItemStatus.PROCESSING
        worker = tab.worker_thread

        tab._on_clear_clicked()

        assert tab._queue.all_items() == [item1]
        assert tab.list_widget.count() == 1
        skipped = [c.args[0] for c in worker.skip_item.call_args_list]
        assert skipped == [item2, item3]

    def test_clear_during_run_does_not_reset_bars(self, tab):
        item1 = _add_one(tab, "mokuro", "vol1")
        _add_one(tab, "mokuro", "vol2")
        tab._on_process_queue_clicked()
        item1.status = ReadingItemStatus.PROCESSING
        tab._on_item_started(0)
        tab._on_item_progress(0, "Loading pages", 42)

        tab._on_clear_clicked()

        assert "Loading pages" in tab.current_progress_widget.status_label.text()
        assert tab.current_progress_widget.progress_bar.value() == 42


class TestCurationContext:
    """D8: reading curation is table-only — inherit the base (None, None)."""

    def test_build_curation_context_is_none_none(self, tab):
        assert tab._build_curation_context() == (None, None)

    def test_build_curation_context_none_none_with_worker(self, tab):
        # Even with a live worker (driven via Process Queue), no media context is
        # sourced (D8): the worker publishes no _curation_video/_subtitle/_offset.
        _add_one(tab)
        tab._on_process_queue_clicked()
        assert tab._build_curation_context() == (None, None)
