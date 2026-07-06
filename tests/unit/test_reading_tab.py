"""Tests for the reading (manga/novel) queue mining tab.

A faithful clone of ``test_audiobook_tab.py`` adapted to the reading domain:
the add flow is a single path classified by ``detector.detect`` (a title dir
expands to N rows) instead of an audio+subtitle file pair. Behaviour under
test:

* Add: each accepted path is classified by ``detect`` into one or more READY
  rows; a ``SetupError`` is surfaced in the log and adds no row.
* Buttons: Preview/Mine enabled iff ≥1 READY item and no run; Clear iff the
  queue is non-empty; Stop visible only during a run; both Add buttons
  disabled during a run.
* Preview / Mine instantiate :class:`ReadingQueueWorker` with the right
  ``preview_mode`` over a READY-items snapshot.
* Per-item signals are READ-ONLY on item state (the worker owns the
  lifecycle): they refresh the row/progress/log but never write status/cards.
* Mid-run removal/clear route dropped items to ``worker.skip_item``.
* ``shutdown()`` releases any curation dialog, then cancels and joins.
* ``update_config()`` rebuilds the processor only when no run is active.
* D8: ``_build_curation_context`` inherits the base ``(None, None)`` — reading
  curation is table-only.

Qt threads are never started — ``ReadingQueueWorker`` is class-level patched
so ``start()`` is a no-op and constructor kwargs can be inspected.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import SetupError
from anki_miner.gui.widgets.reading_tab import ReadingTab
from anki_miner.models.reading_queue import ReadingItemStatus
from anki_miner.services.reading.models import ReadingSourceRef


@pytest.fixture
def tab(qtbot, test_config: AnkiMinerConfig):
    """Instantiate a ReadingTab with a patched queue worker class.

    ``ReadingQueueWorker`` is patched at the module where it is looked up so its
    ``start()`` doesn't spawn a real QThread.
    """
    with patch("anki_miner.gui.widgets.reading_tab.ReadingQueueWorker", autospec=False) as queue_cls:
        queue_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")

        widget = ReadingTab(
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
    """Patch ``detector.detect`` to return *refs* and drive ``_add_source_path``.

    Returns the queue items created by the add (the tail ``len(refs)`` items).
    """
    with patch("anki_miner.gui.widgets.reading_tab.detector.detect", return_value=list(refs)):
        tab._add_source_path(Path(path))
    n = len(refs)
    return tab._queue.all_items()[-n:] if n else []


def _add_one(tab, kind: str = "mokuro", title: str = "Series Vol.1"):
    """Helper: add a single source ref and return its queue item."""
    return _add_refs(tab, [_make_ref(kind, title)])[0]


class TestInitialState:
    """Empty queue: Add enabled, all action buttons disabled."""

    def test_empty_queue_buttons(self, tab):
        assert tab._queue.all_items() == []
        assert tab.add_manga_button.isEnabled()
        assert tab.add_book_button.isEnabled()
        assert not tab.preview_button.isEnabled()
        assert not tab.mine_button.isEnabled()
        assert not tab.clear_button.isEnabled()
        assert tab.stop_button.isHidden()
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

    def test_add_title_dir_expands_to_n_rows(self, tab):
        """A directory that detect resolves to N volumes creates N rows."""
        refs = [_make_ref("mokuro", f"Series Vol.{i}") for i in range(1, 4)]
        items = _add_refs(tab, refs, path="/src/Series")

        assert len(items) == 3
        assert tab.list_widget.count() == 3
        assert [i.title for i in items] == ["Series Vol.1", "Series Vol.2", "Series Vol.3"]

    def test_add_book_ref(self, tab):
        item = _add_one(tab, "epub", "A Novel")
        assert item.kind == "epub"
        assert tab._queue.all_items() == [item]

    def test_add_enables_run_buttons(self, tab):
        _add_one(tab)
        assert tab.preview_button.isEnabled()
        assert tab.mine_button.isEnabled()
        assert tab.clear_button.isEnabled()

    def test_detect_error_surfaced_no_row(self, tab):
        with patch(
            "anki_miner.gui.widgets.reading_tab.detector.detect",
            side_effect=SetupError("No .mokuro sidecar found for 'x.cbz'."),
        ):
            tab._add_source_path(Path("/src/x.cbz"))

        assert tab._queue.all_items() == []
        assert tab.list_widget.count() == 0
        text = tab.log_widget.text_edit.toPlainText()
        assert "sidecar" in text.lower()

    def test_unexpected_detect_error_surfaced_no_row(self, tab):
        with patch(
            "anki_miner.gui.widgets.reading_tab.detector.detect",
            side_effect=RuntimeError("boom"),
        ):
            tab._add_source_path(Path("/src/weird"))

        assert tab._queue.all_items() == []
        assert "boom" in tab.log_widget.text_edit.toPlainText()


class TestAddButtons:
    """Add Manga / Add Book open file dialogs and feed detect."""

    def test_add_manga_button_queues_picked_files(self, tab):
        with (
            patch(
                "anki_miner.gui.widgets.reading_tab.QFileDialog.getOpenFileNames",
                return_value=(["/src/a.mokuro", "/src/b.mokuro"], ""),
            ),
            patch(
                "anki_miner.gui.widgets.reading_tab.detector.detect",
                side_effect=lambda p: [_make_ref("mokuro", p.stem)],
            ),
        ):
            tab._on_add_manga_clicked()

        assert [i.title for i in tab._queue.all_items()] == ["a", "b"]

    def test_add_book_button_queues_picked_file(self, tab):
        with (
            patch(
                "anki_miner.gui.widgets.reading_tab.QFileDialog.getOpenFileNames",
                return_value=(["/src/novel.epub"], ""),
            ),
            patch(
                "anki_miner.gui.widgets.reading_tab.detector.detect",
                return_value=[_make_ref("epub", "novel")],
            ),
        ):
            tab._on_add_book_clicked()

        assert [i.title for i in tab._queue.all_items()] == ["novel"]

    def test_add_manga_cancelled_dialog_noop(self, tab):
        with patch(
            "anki_miner.gui.widgets.reading_tab.QFileDialog.getOpenFileNames",
            return_value=([], ""),
        ):
            tab._on_add_manga_clicked()
        assert tab._queue.all_items() == []

    def test_add_manga_disabled_during_run_is_noop(self, tab):
        _add_one(tab, "mokuro", "a")
        tab._on_mine_clicked()
        assert not tab.add_manga_button.isEnabled()

        with patch("anki_miner.gui.widgets.reading_tab.QFileDialog.getOpenFileNames") as dlg:
            tab._on_add_manga_clicked()
            dlg.assert_not_called()  # guarded out before opening the picker


class TestDragDrop:
    """Dropped files/dirs route through _add_source_path."""

    def test_drop_reading_file_adds_row(self, tab):
        event = MagicMock()
        with (
            patch(
                "anki_miner.gui.widgets.reading_tab.urls_from_event",
                return_value=[_url("/src/a.mokuro")],
            ),
            patch(
                "anki_miner.gui.widgets.reading_tab.detector.detect",
                return_value=[_make_ref("mokuro", "a")],
            ),
        ):
            tab.dropEvent(event)

        assert [i.title for i in tab._queue.all_items()] == ["a"]
        event.acceptProposedAction.assert_called_once()

    def test_drop_none_event_is_noop(self, tab):
        tab.dropEvent(None)  # must not raise
        assert tab._queue.all_items() == []


def _url(local_path: str):
    """A fake QUrl whose toLocalFile returns *local_path*."""
    u = MagicMock()
    u.toLocalFile.return_value = local_path
    return u


class TestRunStartup:
    """Preview / Mine buttons construct the queue worker correctly."""

    def test_mine_constructs_queue_worker_preview_false(self, tab):
        _add_one(tab)
        queue_cls = tab._queue_worker_cls
        tab._on_mine_clicked()

        assert queue_cls.call_count == 1
        kwargs = queue_cls.call_args.kwargs
        assert kwargs["preview_mode"] is False
        assert kwargs["processor"] is tab._processor
        assert kwargs["config"] is tab._config
        # Curation callback gated by the (default-unchecked) review checkbox.
        assert kwargs["curation_callback"] is None
        assert tab.worker_thread is not None
        tab.worker_thread.start.assert_called_once()

    def test_mine_wires_worker_signals_to_slots(self, tab):
        _add_one(tab)
        tab._on_mine_clicked()
        worker = tab.worker_thread

        worker.item_started.connect.assert_called_once_with(tab._on_item_started)
        worker.item_progress.connect.assert_called_once_with(tab._on_item_progress)
        worker.item_finished.connect.assert_called_once_with(tab._on_item_finished)
        worker.queue_finished.connect.assert_called_once_with(tab._on_queue_finished)
        worker.finished.connect.assert_called_once_with(tab._on_worker_finished)

    def test_preview_constructs_queue_worker_preview_true(self, tab):
        _add_one(tab)
        queue_cls = tab._queue_worker_cls
        tab._on_preview_clicked()

        assert queue_cls.call_args.kwargs["preview_mode"] is True

    def test_mine_passes_ready_items_only(self, tab):
        item_done = _add_one(tab, "mokuro", "done")
        item_ready = _add_one(tab, "mokuro", "ready")
        item_done.status = ReadingItemStatus.COMPLETED

        queue_cls = tab._queue_worker_cls
        tab._on_mine_clicked()

        items = queue_cls.call_args.kwargs["items"]
        assert items == [item_ready]

    def test_mine_with_no_ready_items_noop(self, tab):
        queue_cls = tab._queue_worker_cls
        tab._on_mine_clicked()
        assert queue_cls.call_count == 0
        assert tab.worker_thread is None

    def test_run_active_disables_action_buttons(self, tab):
        _add_one(tab)
        tab._on_mine_clicked()

        assert not tab.add_manga_button.isEnabled()
        assert not tab.add_book_button.isEnabled()
        assert not tab.preview_button.isEnabled()
        assert not tab.mine_button.isEnabled()
        assert not tab.stop_button.isHidden()
        assert tab.clear_button.isEnabled()

    def test_run_callback_follows_checkbox(self, tab):
        queue_cls = tab._queue_worker_cls

        _add_one(tab, "mokuro", "a")
        tab.review_words_checkbox.setChecked(True)
        tab._on_mine_clicked()
        # Bound methods compare by ``==`` (fresh wrapper per attribute access).
        assert queue_cls.call_args.kwargs["curation_callback"] == tab._curation_bridge


class TestDeferredProcessor:
    """Tab accepts ``processor=None`` and rebuilds lazily via service_factory."""

    def test_constructs_with_none_processor(self, qtbot, test_config: AnkiMinerConfig):
        sentinel = MagicMock(name="StatsService")
        with patch("anki_miner.gui.widgets.reading_tab.ReadingQueueWorker", autospec=False):
            widget = ReadingTab(
                config=test_config,
                processor=None,
                presenter=MagicMock(name="Presenter"),
                stats_service=sentinel,
            )
            qtbot.addWidget(widget)
            try:
                assert widget._processor is None
                assert widget._stats_service is sentinel
            finally:
                widget.deleteLater()

    def test_lazy_rebuild_threads_stats_service(self, qtbot, test_config: AnkiMinerConfig):
        """When no processor is cached, the build is deferred to the worker via a
        factory (NOT called on the GUI thread) and the factory threads
        stats_service through ``create_episode_processor``."""
        sentinel_stats = MagicMock(name="StatsService")
        with (
            patch("anki_miner.gui.widgets.reading_tab.ReadingQueueWorker", autospec=False) as q_cls,
            patch("anki_miner.gui.widgets.reading_tab.create_episode_processor") as mock_create,
        ):
            q_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")
            built_processor = MagicMock(name="LazyProcessor")
            mock_create.return_value = built_processor

            widget = ReadingTab(
                config=test_config,
                processor=None,
                presenter=MagicMock(name="Presenter"),
                stats_service=sentinel_stats,
            )
            qtbot.addWidget(widget)
            try:
                _add_one(widget)
                widget._on_mine_clicked()

                assert mock_create.call_count == 0
                assert q_cls.call_args.kwargs["processor"] is None
                factory = q_cls.call_args.kwargs["processor_factory"]
                assert factory is not None

                assert factory() is built_processor
                assert mock_create.call_count == 1
                assert mock_create.call_args.kwargs["stats_service"] is sentinel_stats
            finally:
                widget.deleteLater()

    def test_cached_processor_passes_prebuilt_no_factory(self, tab):
        _add_one(tab)
        queue_cls = tab._queue_worker_cls
        tab._on_mine_clicked()

        assert queue_cls.call_args.kwargs["processor"] is tab._processor
        assert queue_cls.call_args.kwargs["processor_factory"] is None

    def test_worker_finished_caches_built_processor_back(self, qtbot, test_config):
        with patch("anki_miner.gui.widgets.reading_tab.ReadingQueueWorker", autospec=False) as q_cls:
            q_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")
            widget = ReadingTab(config=test_config, processor=None, presenter=MagicMock(name="Presenter"))
            qtbot.addWidget(widget)
            try:
                _add_one(widget)
                widget._on_mine_clicked()
                built = MagicMock(name="BuiltProcessor")
                widget.worker_thread.curation_processor = built  # type: ignore[union-attr]

                widget._on_worker_finished()

                assert widget._processor is built
                assert widget.worker_thread is None
            finally:
                widget.deleteLater()


class TestStopAll:
    """Stop forwards to the worker's cancel() and releases any curation dialog."""

    def test_stop_all_calls_worker_cancel(self, tab):
        _add_one(tab)
        tab._on_mine_clicked()
        worker = tab.worker_thread

        tab._on_stop_all_clicked()

        worker.cancel.assert_called_once()  # type: ignore[union-attr]
        assert not tab.stop_button.isEnabled()
        assert tab.stop_button.text() == "Cancelling…"

    def test_stop_releases_active_curation_dialog(self, tab):
        _add_one(tab)
        tab._on_mine_clicked()
        with patch.object(tab, "_cancel_active_curation_dialog") as cancel:
            tab._on_stop_all_clicked()
            cancel.assert_called_once()

    def test_stop_all_noop_when_no_worker(self, tab):
        tab._on_stop_all_clicked()  # must not raise


class TestPerItemSignalsReadOnly:
    """Per-item signals refresh the UI but never write item state (worker owns it)."""

    def test_item_started_does_not_write_status(self, tab):
        """The worker sets PROCESSING before emitting; the slot must not write it.

        Simulate a late-delivered item_started AFTER the worker already advanced
        the item to COMPLETED: the slot must leave it COMPLETED, not clobber it
        back to PROCESSING.
        """
        item_a = _add_one(tab, "mokuro", "vol1")
        _add_one(tab, "mokuro", "vol2")
        _add_one(tab, "mokuro", "vol3")
        tab._on_mine_clicked()

        # Worker owns lifecycle: emulate it having already completed item_a.
        item_a.status = ReadingItemStatus.COMPLETED
        item_a.cards_created = 7

        tab._on_item_started(0)

        # Slot did NOT overwrite the worker-owned status.
        assert item_a.status == ReadingItemStatus.COMPLETED
        assert item_a.cards_created == 7
        assert "Mining 1 of 3" in tab.progress_widget.status_label.text()
        assert "vol1" in tab.progress_widget.status_label.text()

    def test_item_started_refreshes_row_from_worker_status(self, tab):
        item = _add_one(tab, "mokuro", "vol1")
        tab._on_mine_clicked()
        # Worker sets PROCESSING before emitting.
        item.status = ReadingItemStatus.PROCESSING

        tab._on_item_started(0)

        # Row reflects the worker-set status: remove disabled while PROCESSING.
        assert not tab._row_widgets[item].remove_button.isEnabled()

    def test_item_progress_determinate(self, tab):
        _add_one(tab)
        tab._on_mine_clicked()
        tab._on_item_started(0)

        tab._on_item_progress(0, "Loading pages", 42)

        assert tab.progress_widget.progress_bar.maximum() == 100
        assert tab.progress_widget.progress_bar.value() == 42
        assert "Loading pages" in tab.progress_widget.status_label.text()

    def test_item_progress_indeterminate(self, tab):
        _add_one(tab)
        tab._on_mine_clicked()
        tab._on_item_started(0)

        tab._on_item_progress(0, "Fetching definitions", -1)

        assert tab.progress_widget.progress_bar.maximum() == 0
        assert "Fetching definitions" in tab.progress_widget.status_label.text()

    def test_item_finished_success_reads_worker_state(self, tab):
        item = _add_one(tab, "mokuro", "vol1")
        tab._on_mine_clicked()
        tab._on_item_started(0)

        # Worker records the outcome BEFORE emitting item_finished.
        item.status = ReadingItemStatus.COMPLETED
        item.cards_created = 5
        result = MagicMock(cards_created=5)

        tab._on_item_finished(0, result, None, 1)

        assert item.status == ReadingItemStatus.COMPLETED
        assert "5 cards created" in tab._row_widgets[item].detail_label.full_text
        assert "5 cards" in tab.log_widget.text_edit.toPlainText()
        tab._presenter.show_processing_result.assert_called_once_with(result)

    def test_item_finished_does_not_write_state(self, tab):
        """The slot must not write status/cards/error — the worker already did."""
        item = _add_one(tab)
        tab._on_mine_clicked()
        tab._on_item_started(0)
        # Item left at its (worker-owned) status; slot must not mutate it.
        before_status = item.status
        before_cards = item.cards_created

        tab._on_item_finished(0, MagicMock(cards_created=99), None, 1)

        assert item.status == before_status
        assert item.cards_created == before_cards

    def test_item_finished_error_logged(self, tab):
        item = _add_one(tab, "mokuro", "vol1")
        tab._on_mine_clicked()
        tab._on_item_started(0)
        # Worker recorded the error.
        item.status = ReadingItemStatus.ERROR
        item.error_message = "SetupError: DRM"

        tab._on_item_finished(0, None, "SetupError: DRM", 1)

        assert "SetupError: DRM" in tab.log_widget.text_edit.toPlainText()
        assert "SetupError: DRM" in tab._row_widgets[item].detail_label.full_text

    def test_item_finished_presenter_error_swallowed(self, tab):
        item = _add_one(tab)
        tab._on_mine_clicked()
        tab._on_item_started(0)
        item.status = ReadingItemStatus.COMPLETED

        tab._presenter.show_processing_result.side_effect = RuntimeError("presenter blew up")

        tab._on_item_finished(0, MagicMock(cards_created=1), None, 1)  # must not raise

    def test_item_started_out_of_range_idx_is_noop(self, tab):
        item = _add_one(tab)
        tab._on_mine_clicked()
        status_before = tab.progress_widget.status_label.text()

        tab._on_item_started(99)

        assert item.status == ReadingItemStatus.READY
        assert tab.progress_widget.status_label.text() == status_before

    def test_item_finished_out_of_range_idx_is_noop(self, tab):
        _add_one(tab)
        tab._on_mine_clicked()
        tab._on_item_started(0)

        tab._on_item_finished(99, None, "err", 1)

        tab._presenter.show_processing_result.assert_not_called()

    def test_item_finished_with_no_run_snapshot_is_noop(self, tab):
        tab._on_item_finished(99, None, "err", 1)
        tab._presenter.show_processing_result.assert_not_called()


class TestQueueFinished:
    """``queue_finished`` logs the run summary; state cleanup is elsewhere."""

    def test_queue_finished_summary_logged(self, tab):
        good = _add_one(tab, "mokuro", "good")
        bad = _add_one(tab, "mokuro", "bad")
        tab._on_mine_clicked()
        # Worker owns state; emulate its recorded outcomes.
        good.status = ReadingItemStatus.COMPLETED
        bad.status = ReadingItemStatus.ERROR
        tab._on_queue_finished()

        text = tab.log_widget.text_edit.toPlainText()
        assert "1 succeeded" in text
        assert "1 failed" in text

    def test_queue_finished_does_not_mutate_state(self, tab):
        _add_one(tab)
        tab._on_mine_clicked()
        worker = tab.worker_thread

        tab._on_queue_finished()

        assert tab.worker_thread is worker
        assert tab._run_items != []


class TestWorkerFinished:
    """``QThread.finished`` is the single cleanup signal for every run-exit path."""

    def test_worker_finished_clears_worker(self, tab):
        _add_one(tab)
        tab._on_mine_clicked()
        assert tab.worker_thread is not None

        tab._on_worker_finished()

        assert tab.worker_thread is None
        assert tab._run_items == []

    def test_worker_finished_restores_stop_button_and_progress(self, tab):
        _add_one(tab)
        tab._on_mine_clicked()
        tab._on_item_started(0)
        tab._on_item_progress(0, "Loading", -1)
        tab._on_stop_all_clicked()
        assert tab.stop_button.text() == "Cancelling…"

        tab._on_worker_finished()

        assert tab.stop_button.text() == "Stop All"
        assert tab.stop_button.isEnabled()
        assert tab.progress_widget.progress_bar.maximum() == 100
        assert tab.progress_widget.status_label.text() == "Ready"


class TestRemoveAndClear:
    """Remove button and Clear button manage queue contents."""

    def test_remove_item(self, tab):
        item = _add_one(tab, "mokuro", "vol1")
        keep = _add_one(tab, "mokuro", "vol2")

        tab._on_remove_clicked(item)

        assert tab._queue.all_items() == [keep]
        assert tab.list_widget.count() == 1
        assert item not in tab._row_widgets

    def test_remove_processing_item_is_noop(self, tab):
        item = _add_one(tab)
        tab._on_mine_clicked()
        item.status = ReadingItemStatus.PROCESSING

        tab._on_remove_clicked(item)

        assert tab._queue.all_items() == [item]

    def test_remove_during_run_skips_item_in_worker(self, tab):
        _add_one(tab, "mokuro", "vol1")
        item2 = _add_one(tab, "mokuro", "vol2")
        tab._on_mine_clicked()
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
        tab._on_mine_clicked()
        item1.status = ReadingItemStatus.PROCESSING
        worker = tab.worker_thread

        tab._on_clear_clicked()

        assert tab._queue.all_items() == [item1]
        assert tab.list_widget.count() == 1
        skipped = [c.args[0] for c in worker.skip_item.call_args_list]
        assert skipped == [item2, item3]

    def test_clear_during_run_does_not_reset_progress(self, tab):
        item1 = _add_one(tab, "mokuro", "vol1")
        _add_one(tab, "mokuro", "vol2")
        tab._on_mine_clicked()
        item1.status = ReadingItemStatus.PROCESSING
        tab._on_item_started(0)
        tab._on_item_progress(0, "Loading pages", 42)

        tab._on_clear_clicked()

        assert "Loading pages" in tab.progress_widget.status_label.text()
        assert tab.progress_widget.progress_bar.value() == 42


class TestShutdown:
    """shutdown() releases curation, then cancels and joins the worker."""

    def test_shutdown_with_active_worker(self, tab):
        _add_one(tab)
        tab._on_mine_clicked()
        worker = tab.worker_thread

        tab.shutdown()

        worker.cancel.assert_called_once()  # type: ignore[union-attr]
        worker.wait.assert_called()  # type: ignore[union-attr]
        assert tab.worker_thread is None

    def test_shutdown_releases_curation_before_joining_worker(self, tab):
        with patch.object(tab, "_cancel_active_curation_dialog") as cancel:
            worker = MagicMock(name="QueueWorker")
            order = MagicMock()
            order.attach_mock(cancel, "release")
            order.attach_mock(worker.wait, "wait")
            tab.worker_thread = worker
            tab.shutdown()
            cancel.assert_called_once()
            worker.cancel.assert_called_once()
            called = [c[0] for c in order.mock_calls]
            assert called.index("release") < called.index("wait")

    def test_shutdown_poisons_curation_gate(self, tab):
        tab.worker_thread = MagicMock(name="QueueWorker")
        tab.shutdown()
        assert tab._curation_gate_poisoned is True
        assert tab._curation_event.is_set()

    def test_shutdown_with_nothing_active(self, tab):
        tab.shutdown()  # must not raise

    def test_worker_thread_attr_present(self, tab):
        # BackgroundTaskController duck-types on this public attribute.
        assert hasattr(tab, "worker_thread")


class TestCurationContext:
    """D8: reading curation is table-only — inherit the base (None, None)."""

    def test_build_curation_context_is_none_none(self, tab):
        assert tab._build_curation_context() == (None, None)

    def test_build_curation_context_none_none_with_worker(self, tab):
        # Even with a live worker, no media context is sourced (D8): the worker
        # publishes no _curation_video/_curation_subtitle/_curation_offset.
        _add_one(tab)
        tab._on_mine_clicked()
        assert tab._build_curation_context() == (None, None)


class TestUpdateConfig:
    """update_config rebuilds the processor only when idle."""

    def test_update_config_idle_drops_processor_to_none(self, tab, test_config):
        old_processor = tab._processor
        new_cfg = replace(test_config, subtitle_offset=2.5)
        with patch("anki_miner.gui.widgets.reading_tab.create_episode_processor") as mock_create:
            tab.update_config(new_cfg)

        assert tab._config is new_cfg
        assert tab._processor is None
        mock_create.assert_not_called()
        old_processor.close.assert_called_once()
        old_processor.release_dictionary_resources.assert_not_called()

    def test_update_config_busy_sets_dirty_flag(self, tab, test_config):
        _add_one(tab)
        tab._on_mine_clicked()
        tab.worker_thread.isRunning.return_value = True  # type: ignore[union-attr]
        original_processor = tab._processor

        new_cfg = replace(test_config, subtitle_offset=2.5)
        with patch("anki_miner.gui.widgets.reading_tab.create_episode_processor") as mock_create:
            tab.update_config(new_cfg)

        assert tab._config is new_cfg
        assert tab._processor is original_processor
        assert tab._config_dirty is True
        original_processor.close.assert_not_called()
        mock_create.assert_not_called()

    def test_worker_finished_reconciles_dirty_config(self, tab, test_config):
        _add_one(tab)
        tab._on_mine_clicked()
        tab.worker_thread.isRunning.return_value = True
        original_processor = tab._processor

        new_cfg = replace(test_config, subtitle_offset=2.5)
        tab.update_config(new_cfg)
        assert tab._config_dirty is True

        tab.worker_thread.isRunning.return_value = False
        tab._on_worker_finished()

        original_processor.close.assert_called_once()
        assert tab._processor is None
        assert tab._config_dirty is False


class TestReleaseDictionaryResources:
    """Settings → Remove dictionary drops sqlite handles (Issue #30)."""

    def test_release_when_idle(self, tab):
        processor = tab._processor
        assert tab.release_dictionary_resources() is True
        processor.release_dictionary_resources.assert_called_once()
        assert tab._processor is None

    def test_release_refused_during_run(self, tab):
        _add_one(tab)
        tab._on_mine_clicked()
        tab.worker_thread.isRunning.return_value = True  # type: ignore[union-attr]

        assert tab.release_dictionary_resources() is False
        assert tab._processor is not None
