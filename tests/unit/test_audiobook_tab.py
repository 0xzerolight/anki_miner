"""Tests for the audiobook queue mining tab.

Mirrors ``test_youtube_tab.py`` minus the URL/probe/playlist add flow: local
file pairs need no probe stage, so items enter the queue READY. Behaviour
under test:

* Add: validates both paths exist, creates a READY item, renders a row,
  clears both file pickers; rejections log an error and leave the pickers.
* Auto-fill: picking an audio file fills the subtitle picker with the
  same-stem subtitle next to it — only when the subtitle field is empty.
* Buttons: Mine enabled iff ≥1 READY item and no run; Clear iff the
  queue is non-empty; Stop visible only during a run.
* Mine instantiates :class:`AudiobookQueueWorker` over a READY-items
  snapshot.
* Per-item signals update the queue model + row widgets + progress widget.
* Mid-run removal/clear route dropped items to ``worker.skip_item``.
* ``shutdown()`` releases any curation dialog, then cancels and joins.
* ``update_config()`` rebuilds the processor only when no run is active.

Qt threads are never started — ``AudiobookQueueWorker`` is class-level
patched so ``start()`` is a no-op and constructor kwargs can be inspected.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.audiobook_tab import AudiobookTab
from anki_miner.models.audiobook_queue import AudiobookItemStatus


@pytest.fixture
def tab(qtbot, test_config: AnkiMinerConfig):
    """Instantiate an AudiobookTab with a patched queue worker class.

    ``AudiobookQueueWorker`` is patched at the module where it is looked up
    so its ``start()`` doesn't spawn a real QThread.
    """
    with patch("anki_miner.gui.widgets.audiobook_tab.AudiobookQueueWorker", autospec=False) as queue_cls:
        queue_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")

        widget = AudiobookTab(
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


def _make_pair(tmp_path: Path, stem: str = "book", audio_ext: str = ".m4b", sub_ext: str = ".srt") -> tuple[Path, Path]:
    """Create an audio+subtitle file pair on disk and return their paths."""
    audio = tmp_path / f"{stem}{audio_ext}"
    sub = tmp_path / f"{stem}{sub_ext}"
    audio.touch()
    sub.touch()
    return audio, sub


def _add_pair(tab, tmp_path: Path, stem: str = "book"):
    """Helper: create a pair on disk, fill the pickers, click Add; return the item."""
    audio, sub = _make_pair(tmp_path, stem)
    tab.audio_selector.set_path(str(audio))
    tab.subtitle_selector.set_path(str(sub))
    tab._on_add_clicked()
    return tab._queue.all_items()[-1]


class TestInitialState:
    """Empty queue: Add enabled, all action buttons disabled."""

    def test_empty_queue_buttons(self, tab):
        assert tab._queue.all_items() == []
        assert tab.add_button.isEnabled()
        assert not tab.mine_button.isEnabled()
        assert not tab.clear_button.isEnabled()
        assert tab.stop_button.isHidden()
        assert tab.worker_thread is None

    def test_list_widget_empty(self, tab):
        assert tab.list_widget.count() == 0
        assert not tab.empty_label.isHidden()

    def test_review_checkbox_default_unchecked(self, tab):
        assert tab.review_words_checkbox.isChecked() is False


class TestAddPair:
    """Add validates the pair, queues a READY item, and clears the pickers."""

    def test_add_valid_pair_creates_ready_item(self, tab, tmp_path):
        audio, sub = _make_pair(tmp_path)
        tab.audio_selector.set_path(str(audio))
        tab.subtitle_selector.set_path(str(sub))

        tab._on_add_clicked()

        items = tab._queue.all_items()
        assert len(items) == 1
        assert items[0].audio_file == audio
        assert items[0].subtitle_file == sub
        assert items[0].status == AudiobookItemStatus.READY

    def test_add_renders_row_and_clears_pickers(self, tab, tmp_path):
        audio, sub = _make_pair(tmp_path)
        tab.audio_selector.set_path(str(audio))
        tab.subtitle_selector.set_path(str(sub))

        tab._on_add_clicked()

        assert tab.list_widget.count() == 1
        item = tab._queue.all_items()[0]
        assert item in tab._row_widgets
        assert tab.audio_selector.get_path() == ""
        assert tab.subtitle_selector.get_path() == ""

    def test_add_enables_run_buttons(self, tab, tmp_path):
        _add_pair(tab, tmp_path)
        assert tab.mine_button.isEnabled()
        assert tab.clear_button.isEnabled()

    def test_add_missing_audio_rejected(self, tab, tmp_path):
        _, sub = _make_pair(tmp_path)
        tab.audio_selector.set_path(str(tmp_path / "missing.m4b"))
        tab.subtitle_selector.set_path(str(sub))

        tab._on_add_clicked()

        assert tab._queue.all_items() == []
        assert tab.list_widget.count() == 0
        assert "audio" in tab.log_widget.text_edit.toPlainText().lower()
        # Pickers NOT cleared so the user can see/fix what they selected.
        assert tab.audio_selector.get_path() != ""

    def test_add_missing_subtitle_rejected(self, tab, tmp_path):
        audio, _ = _make_pair(tmp_path)
        tab.audio_selector.set_path(str(audio))
        tab.subtitle_selector.set_path(str(tmp_path / "missing.srt"))

        tab._on_add_clicked()

        assert tab._queue.all_items() == []
        assert "subtitle" in tab.log_widget.text_edit.toPlainText().lower()

    def test_add_empty_paths_noop(self, tab):
        tab._on_add_clicked()
        assert tab._queue.all_items() == []

    def test_add_disabled_during_run_is_noop(self, tab, tmp_path):
        _add_pair(tab, tmp_path, "a")
        tab._on_mine_clicked()
        assert not tab.add_button.isEnabled()

        audio, sub = _make_pair(tmp_path, "b")
        tab.audio_selector.set_path(str(audio))
        tab.subtitle_selector.set_path(str(sub))
        tab._on_add_clicked()

        assert len(tab._queue.all_items()) == 1  # nothing added mid-run


class TestAutoFill:
    """Choosing an audio file auto-fills an empty subtitle picker."""

    def test_autofill_same_stem_srt(self, tab, tmp_path):
        audio, sub = _make_pair(tmp_path)

        tab.audio_selector.set_path(str(audio))

        assert tab.subtitle_selector.get_path() == str(sub)

    def test_autofill_other_extensions(self, tab, tmp_path):
        audio, sub = _make_pair(tmp_path, audio_ext=".mp3", sub_ext=".ass")

        tab.audio_selector.set_path(str(audio))

        assert tab.subtitle_selector.get_path() == str(sub)

    def test_autofill_does_not_overwrite_non_empty(self, tab, tmp_path):
        audio, _ = _make_pair(tmp_path)
        other = tmp_path / "other.srt"
        other.touch()
        tab.subtitle_selector.set_path(str(other))

        tab.audio_selector.set_path(str(audio))

        assert tab.subtitle_selector.get_path() == str(other)

    def test_no_autofill_when_no_match(self, tab, tmp_path):
        audio = tmp_path / "lonely.m4b"
        audio.touch()

        tab.audio_selector.set_path(str(audio))

        assert tab.subtitle_selector.get_path() == ""


class TestRunStartup:
    """The Mine button constructs the queue worker correctly."""

    def test_mine_constructs_queue_worker(self, tab, tmp_path):
        _add_pair(tab, tmp_path)
        queue_cls = tab._queue_worker_cls
        tab._on_mine_clicked()

        assert queue_cls.call_count == 1
        kwargs = queue_cls.call_args.kwargs
        assert kwargs["processor"] is tab._processor
        assert kwargs["config"] is tab._config
        # Curation callback gated by the (default-unchecked) review checkbox.
        assert kwargs["curation_callback"] is None
        assert tab.worker_thread is not None
        tab.worker_thread.start.assert_called_once()

    def test_mine_wires_worker_signals_to_slots(self, tab, tmp_path):
        """Each worker signal is connected to the matching tab slot.

        The worker is mocked, so a typo'd signal name in _start_run would
        otherwise go unnoticed — assert every connect explicitly.
        """
        _add_pair(tab, tmp_path)
        tab._on_mine_clicked()
        worker = tab.worker_thread

        worker.item_started.connect.assert_called_once_with(tab._on_item_started)
        worker.item_progress.connect.assert_called_once_with(tab._on_item_progress)
        worker.item_finished.connect.assert_called_once_with(tab._on_item_finished)
        worker.queue_finished.connect.assert_called_once_with(tab._on_queue_finished)
        worker.finished.connect.assert_called_once_with(tab._on_worker_finished)

    def test_mine_passes_ready_items_only(self, tab, tmp_path):
        item_done = _add_pair(tab, tmp_path, "done")
        item_ready = _add_pair(tab, tmp_path, "ready")
        item_done.status = AudiobookItemStatus.COMPLETED

        queue_cls = tab._queue_worker_cls
        tab._on_mine_clicked()

        items = queue_cls.call_args.kwargs["items"]
        assert items == [item_ready]

    def test_mine_with_no_ready_items_noop(self, tab):
        queue_cls = tab._queue_worker_cls
        tab._on_mine_clicked()
        assert queue_cls.call_count == 0
        assert tab.worker_thread is None

    def test_run_active_disables_action_buttons(self, tab, tmp_path):
        _add_pair(tab, tmp_path)
        tab._on_mine_clicked()

        assert not tab.add_button.isEnabled()
        assert not tab.mine_button.isEnabled()
        assert not tab.stop_button.isHidden()
        # Clear still works mid-run (trims non-PROCESSING rows).
        assert tab.clear_button.isEnabled()

    def test_run_callback_follows_checkbox(self, tab, tmp_path):
        queue_cls = tab._queue_worker_cls

        _add_pair(tab, tmp_path, "a")
        tab.review_words_checkbox.setChecked(True)
        tab._on_mine_clicked()
        # Bound methods compare by ``==`` (fresh wrapper per attribute access).
        assert queue_cls.call_args.kwargs["curation_callback"] == tab._curation_bridge


class TestDeferredProcessor:
    """Tab accepts ``processor=None`` and rebuilds lazily via service_factory."""

    def test_constructs_with_none_processor(self, qtbot, test_config: AnkiMinerConfig):
        sentinel = MagicMock(name="StatsService")
        with patch("anki_miner.gui.widgets.audiobook_tab.AudiobookQueueWorker", autospec=False):
            widget = AudiobookTab(
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

    def test_lazy_rebuild_threads_stats_service(self, qtbot, test_config: AnkiMinerConfig, tmp_path):
        """When no processor is cached, the build is deferred to the worker via a
        factory (NOT called on the GUI thread) and the factory threads
        stats_service through ``create_episode_processor``."""
        sentinel_stats = MagicMock(name="StatsService")
        with (
            patch("anki_miner.gui.widgets.audiobook_tab.AudiobookQueueWorker", autospec=False) as q_cls,
            patch("anki_miner.gui.widgets.audiobook_tab.create_episode_processor") as mock_create,
        ):
            q_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")
            built_processor = MagicMock(name="LazyProcessor")
            mock_create.return_value = built_processor

            widget = AudiobookTab(
                config=test_config,
                processor=None,
                presenter=MagicMock(name="Presenter"),
                stats_service=sentinel_stats,
            )
            qtbot.addWidget(widget)
            try:
                _add_pair(widget, tmp_path)
                widget._on_mine_clicked()

                # GUI thread did NOT build the processor — a factory was passed.
                assert mock_create.call_count == 0
                assert q_cls.call_args.kwargs["processor"] is None
                factory = q_cls.call_args.kwargs["processor_factory"]
                assert factory is not None

                # Invoking the factory (as run() would) builds via the service
                # factory, threading stats_service through.
                assert factory() is built_processor
                assert mock_create.call_count == 1
                assert mock_create.call_args.kwargs["stats_service"] is sentinel_stats
            finally:
                widget.deleteLater()


class TestOffThreadProcessorBuild:
    """No cached processor → factory passed to worker; built processor cached back."""

    def test_no_cached_processor_passes_factory_not_prebuilt(self, qtbot, test_config, tmp_path):
        with (
            patch("anki_miner.gui.widgets.audiobook_tab.AudiobookQueueWorker", autospec=False) as q_cls,
            patch("anki_miner.gui.widgets.audiobook_tab.create_episode_processor") as mock_create,
        ):
            q_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")
            widget = AudiobookTab(config=test_config, processor=None, presenter=MagicMock(name="Presenter"))
            qtbot.addWidget(widget)
            try:
                _add_pair(widget, tmp_path)
                widget._on_mine_clicked()

                assert q_cls.call_args.kwargs["processor"] is None
                assert q_cls.call_args.kwargs["processor_factory"] is not None
                # The GUI thread never built the processor.
                mock_create.assert_not_called()
            finally:
                widget.deleteLater()

    def test_cached_processor_passes_prebuilt_no_factory(self, tab, tmp_path):
        """When a processor is already cached, it is passed directly (no factory)."""
        _add_pair(tab, tmp_path)
        queue_cls = tab._queue_worker_cls
        tab._on_mine_clicked()

        assert queue_cls.call_args.kwargs["processor"] is tab._processor
        assert queue_cls.call_args.kwargs["processor_factory"] is None

    def test_worker_finished_caches_built_processor_back(self, qtbot, test_config, tmp_path):
        """After a factory-built run, the built processor is cached into
        self._processor so subsequent runs reuse it."""
        with patch("anki_miner.gui.widgets.audiobook_tab.AudiobookQueueWorker", autospec=False) as q_cls:
            q_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")
            widget = AudiobookTab(config=test_config, processor=None, presenter=MagicMock(name="Presenter"))
            qtbot.addWidget(widget)
            try:
                _add_pair(widget, tmp_path)
                widget._on_mine_clicked()
                built = MagicMock(name="BuiltProcessor")
                widget.worker_thread.curation_processor = built  # type: ignore[union-attr]

                widget._on_worker_finished()

                assert widget._processor is built
                assert widget.worker_thread is None
            finally:
                widget.deleteLater()

    def test_worker_finished_keeps_existing_cached_processor(self, tab):
        """When a processor was already cached (prebuilt path), the cache-back
        step is a no-op and does not overwrite it."""
        original = tab._processor
        # No active worker; the guard reads worker_thread is None and skips.
        tab._on_worker_finished()
        assert tab._processor is original


class TestStopAll:
    """Stop forwards to the worker's cancel() and releases any curation dialog."""

    def test_stop_all_calls_worker_cancel(self, tab, tmp_path):
        _add_pair(tab, tmp_path)
        tab._on_mine_clicked()
        worker = tab.worker_thread

        tab._on_stop_all_clicked()

        worker.cancel.assert_called_once()  # type: ignore[union-attr]
        assert not tab.stop_button.isEnabled()
        assert tab.stop_button.text() == "Cancelling…"

    def test_stop_releases_active_curation_dialog(self, tab, tmp_path):
        _add_pair(tab, tmp_path)
        tab._on_mine_clicked()
        with patch.object(tab, "_cancel_active_curation_dialog") as cancel:
            tab._on_stop_all_clicked()
            cancel.assert_called_once()

    def test_stop_all_noop_when_no_worker(self, tab):
        # Should not raise.
        tab._on_stop_all_clicked()


class TestPerItemSignals:
    """Per-item signals update the row widgets and progress widget."""

    def test_item_started_marks_processing(self, tab, tmp_path):
        item_a = _add_pair(tab, tmp_path, "vol1")
        _add_pair(tab, tmp_path, "vol2")
        _add_pair(tab, tmp_path, "vol3")
        tab._on_mine_clicked()

        tab._on_item_started(0)

        assert item_a.status == AudiobookItemStatus.PROCESSING
        assert "Mining 1 of 3" in tab.progress_widget.status_label.text()
        assert "vol1.m4b" in tab.progress_widget.status_label.text()
        # Row's remove button disabled while PROCESSING.
        assert not tab._row_widgets[item_a].remove_button.isEnabled()

    def test_item_progress_determinate(self, tab, tmp_path):
        _add_pair(tab, tmp_path)
        tab._on_mine_clicked()
        tab._on_item_started(0)

        tab._on_item_progress(0, "Extracting audio", 42)

        assert tab.progress_widget.progress_bar.maximum() == 100
        assert tab.progress_widget.progress_bar.value() == 42
        assert "Extracting audio" in tab.progress_widget.status_label.text()

    def test_item_progress_indeterminate(self, tab, tmp_path):
        _add_pair(tab, tmp_path)
        tab._on_mine_clicked()
        tab._on_item_started(0)

        tab._on_item_progress(0, "Fetching definitions", -1)

        assert tab.progress_widget.progress_bar.maximum() == 0  # indeterminate
        assert "Fetching definitions" in tab.progress_widget.status_label.text()

    def test_item_finished_success_marks_completed(self, tab, tmp_path):
        item = _add_pair(tab, tmp_path)
        tab._on_mine_clicked()
        tab._on_item_started(0)

        result = MagicMock(cards_created=5)
        tab._on_item_finished(0, result, None, 1)

        assert item.status == AudiobookItemStatus.COMPLETED
        assert item.cards_created == 5
        assert "5 cards created" in tab._row_widgets[item].detail_label.full_text
        # Presenter is forwarded the result.
        tab._presenter.show_processing_result.assert_called_once_with(result)

    def test_item_finished_error_marks_error(self, tab, tmp_path):
        item = _add_pair(tab, tmp_path)
        tab._on_mine_clicked()
        tab._on_item_started(0)

        tab._on_item_finished(0, None, "FFmpegError: oops", 1)

        assert item.status == AudiobookItemStatus.ERROR
        assert item.error_message == "FFmpegError: oops"
        assert "FFmpegError: oops" in tab._row_widgets[item].detail_label.full_text

    def test_item_finished_failed_result_marks_error(self, tab, tmp_path):
        """A non-raising failed ProcessingResult (error=None) routes to ERROR."""
        from anki_miner.models import ProcessingResult

        item = _add_pair(tab, tmp_path)
        tab._on_mine_clicked()
        tab._on_item_started(0)

        result = ProcessingResult(total_words_found=0, new_words_found=0, cards_created=0, errors=["anki went away"])
        tab._on_item_finished(0, result, None, 1)

        assert item.status == AudiobookItemStatus.ERROR
        assert item.error_message == "anki went away"
        tab._presenter.show_processing_result.assert_not_called()

    def test_item_finished_cancelled_result_marks_ready(self, tab, tmp_path):
        """A Stop-mid-mine cancelled result leaves the item re-minable (READY)."""
        from anki_miner.models import ProcessingResult
        from anki_miner.models.processing import CANCELLED_ERROR

        item = _add_pair(tab, tmp_path)
        tab._on_mine_clicked()
        tab._on_item_started(0)

        result = ProcessingResult(total_words_found=0, new_words_found=0, cards_created=0, errors=[CANCELLED_ERROR])
        tab._on_item_finished(0, result, None, 1)

        assert item.status == AudiobookItemStatus.READY
        assert item.error_message is None

    def test_item_finished_presenter_error_swallowed(self, tab, tmp_path):
        item = _add_pair(tab, tmp_path)
        tab._on_mine_clicked()
        tab._on_item_started(0)

        tab._presenter.show_processing_result.side_effect = RuntimeError("presenter blew up")

        # Must not propagate.
        tab._on_item_finished(0, MagicMock(cards_created=1), None, 1)
        assert item.status == AudiobookItemStatus.COMPLETED

    def test_item_started_out_of_range_idx_is_noop(self, tab, tmp_path):
        """An idx beyond the run snapshot must not raise or touch any state."""
        item = _add_pair(tab, tmp_path)
        tab._on_mine_clicked()
        status_before = tab.progress_widget.status_label.text()

        tab._on_item_started(99)

        assert item.status == AudiobookItemStatus.READY
        assert tab.progress_widget.status_label.text() == status_before

    def test_item_started_with_no_run_snapshot_is_noop(self, tab):
        # No run started — _run_items is empty.
        tab._on_item_started(99)
        assert tab._queue.all_items() == []

    def test_item_finished_out_of_range_idx_is_noop(self, tab, tmp_path):
        item = _add_pair(tab, tmp_path)
        tab._on_mine_clicked()
        tab._on_item_started(0)

        tab._on_item_finished(99, None, "err", 1)

        assert item.status == AudiobookItemStatus.PROCESSING
        assert item.error_message is None
        tab._presenter.show_processing_result.assert_not_called()

    def test_item_finished_with_no_run_snapshot_is_noop(self, tab):
        tab._on_item_finished(99, None, "err", 1)
        tab._presenter.show_processing_result.assert_not_called()


class TestQueueFinished:
    """``queue_finished`` logs the run summary; state cleanup is elsewhere."""

    def test_queue_finished_summary_logged(self, tab, tmp_path):
        _add_pair(tab, tmp_path, "good")
        _add_pair(tab, tmp_path, "bad")
        tab._on_mine_clicked()
        tab._on_item_started(0)
        tab._on_item_finished(0, MagicMock(cards_created=2), None, 1)
        tab._on_item_started(1)
        tab._on_item_finished(1, None, "boom", 1)
        tab._on_queue_finished()

        text = tab.log_widget.text_edit.toPlainText()
        assert "1 succeeded" in text
        assert "1 failed" in text

    def test_queue_finished_does_not_mutate_state(self, tab, tmp_path):
        _add_pair(tab, tmp_path)
        tab._on_mine_clicked()
        worker = tab.worker_thread

        tab._on_queue_finished()

        assert tab.worker_thread is worker
        assert tab._run_items != []

    def test_queue_finished_counts_current_run_only(self, tab, tmp_path):
        """A prior run's finished rows must not inflate the next run's summary."""
        _add_pair(tab, tmp_path, "old1")
        _add_pair(tab, tmp_path, "old2")
        tab._on_mine_clicked()
        tab._on_item_started(0)
        tab._on_item_finished(0, MagicMock(cards_created=1), None, 1)
        tab._on_item_started(1)
        tab._on_item_finished(1, MagicMock(cards_created=1), None, 1)
        tab._on_queue_finished()
        tab._on_worker_finished()

        _add_pair(tab, tmp_path, "new")
        tab._on_mine_clicked()
        tab._on_item_started(0)
        tab._on_item_finished(0, MagicMock(cards_created=3), None, 1)
        tab._on_queue_finished()

        last_line = tab.log_widget.text_edit.toPlainText().strip().splitlines()[-1]
        assert "1 succeeded" in last_line
        assert "0 failed" in last_line
        tab._on_worker_finished()
        assert tab.progress_widget.status_label.text() == "Complete — 1 succeeded"


class TestWorkerFinished:
    """``QThread.finished`` is the single cleanup signal for every run-exit path."""

    def test_worker_finished_clears_worker(self, tab, tmp_path):
        _add_pair(tab, tmp_path)
        tab._on_mine_clicked()
        assert tab.worker_thread is not None

        tab._on_worker_finished()

        assert tab.worker_thread is None
        assert tab._run_items == []

    def test_worker_finished_recomputes_buttons(self, tab, tmp_path):
        item = _add_pair(tab, tmp_path)
        tab._on_mine_clicked()
        tab._on_item_started(0)
        tab._on_item_finished(0, MagicMock(cards_created=2), None, 1)
        tab._on_queue_finished()
        tab._on_worker_finished()

        # No more READY items; Mine disabled, Add re-enabled, Stop hidden.
        assert tab.add_button.isEnabled()
        assert not tab.mine_button.isEnabled()
        assert tab.stop_button.isHidden()
        assert item.status == AudiobookItemStatus.COMPLETED

    def test_worker_finished_restores_stop_button_and_progress(self, tab, tmp_path):
        _add_pair(tab, tmp_path)
        tab._on_mine_clicked()
        tab._on_item_started(0)
        tab._on_item_progress(0, "Extracting", -1)
        tab._on_stop_all_clicked()
        assert tab.stop_button.text() == "Cancelling…"

        tab._on_worker_finished()

        assert tab.stop_button.text() == "Stop All"
        assert tab.stop_button.isEnabled()
        assert tab.progress_widget.progress_bar.maximum() == 100
        assert tab.progress_widget.status_label.text() == "Cancelled"


class TestRemoveAndClear:
    """Remove button and Clear button manage queue contents."""

    def test_remove_item(self, tab, tmp_path):
        item = _add_pair(tab, tmp_path, "vol1")
        keep = _add_pair(tab, tmp_path, "vol2")

        tab._on_remove_clicked(item)

        assert tab._queue.all_items() == [keep]
        assert tab.list_widget.count() == 1
        assert item not in tab._row_widgets

    def test_remove_processing_item_is_noop(self, tab, tmp_path):
        item = _add_pair(tab, tmp_path)
        tab._on_mine_clicked()
        tab._on_item_started(0)

        tab._on_remove_clicked(item)

        assert tab._queue.all_items() == [item]

    def test_remove_during_run_skips_item_in_worker(self, tab, tmp_path):
        _add_pair(tab, tmp_path, "vol1")
        item2 = _add_pair(tab, tmp_path, "vol2")
        tab._on_mine_clicked()
        tab._on_item_started(0)
        worker = tab.worker_thread

        tab._on_remove_clicked(item2)

        worker.skip_item.assert_called_once_with(item2)
        assert item2 not in tab._queue.all_items()

    def test_clear_removes_non_processing(self, tab, tmp_path):
        _add_pair(tab, tmp_path, "vol1")
        _add_pair(tab, tmp_path, "vol2")

        tab._on_clear_clicked()

        assert tab._queue.all_items() == []
        assert tab.list_widget.count() == 0
        assert not tab.clear_button.isEnabled()

    def test_clear_during_run_preserves_processing(self, tab, tmp_path):
        item1 = _add_pair(tab, tmp_path, "vol1")
        item2 = _add_pair(tab, tmp_path, "vol2")
        item3 = _add_pair(tab, tmp_path, "vol3")
        tab._on_mine_clicked()
        tab._on_item_started(0)  # item1 -> PROCESSING
        worker = tab.worker_thread

        tab._on_clear_clicked()

        assert tab._queue.all_items() == [item1]
        assert tab.list_widget.count() == 1
        skipped = [c.args[0] for c in worker.skip_item.call_args_list]
        assert skipped == [item2, item3]

    def test_clear_resets_progress_widget_when_idle(self, tab, tmp_path):
        _add_pair(tab, tmp_path)
        tab.progress_widget.set_indeterminate()
        tab.progress_widget.set_status("Extracting")

        tab._on_clear_clicked()

        assert tab.progress_widget.progress_bar.maximum() == 100
        assert tab.progress_widget.status_label.text() == "Ready"

    def test_clear_during_run_does_not_reset_progress(self, tab, tmp_path):
        _add_pair(tab, tmp_path, "vol1")
        _add_pair(tab, tmp_path, "vol2")
        tab._on_mine_clicked()
        tab._on_item_started(0)
        tab._on_item_progress(0, "Extracting audio", 42)

        tab._on_clear_clicked()

        assert "Extracting audio" in tab.progress_widget.status_label.text()
        # Composed whole-run value: item 1 of 2 at 42% -> 21%.
        assert tab.progress_widget.progress_bar.value() == 21


class TestShutdown:
    """shutdown() releases curation, then cancels and joins the worker."""

    def test_shutdown_with_active_worker(self, tab, tmp_path):
        _add_pair(tab, tmp_path)
        tab._on_mine_clicked()
        worker = tab.worker_thread

        tab.shutdown()

        worker.cancel.assert_called_once()  # type: ignore[union-attr]
        worker.wait.assert_called()  # type: ignore[union-attr]
        assert tab.worker_thread is None

    def test_shutdown_releases_curation_before_joining_worker(self, tab):
        """The dialog release must precede the join, else a worker parked in
        _curation_event.wait() hangs the GUI forever (Issue #65)."""
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
        # Should not raise.
        tab.shutdown()


class TestCurationContext:
    """_build_curation_context sources player + lookup from the live worker."""

    def test_no_worker_returns_none(self, tab):
        tab.worker_thread = None
        assert tab._build_curation_context() == (None, None)

    def test_routes_through_shared_helpers(self, tab, facade_processor, tmp_path):
        audio = tmp_path / "book.m4b"
        subs = tmp_path / "book.srt"
        tab.worker_thread = SimpleNamespace(
            curation_processor=facade_processor,
            _curation_video=audio,
            _curation_subtitle=subs,
            _curation_offset=4.0,
        )

        sentinel_ctx = object()
        with patch.object(AudiobookTab, "_make_curation_media_context", return_value=sentinel_ctx) as helper:
            media_context, lookup_fn = tab._build_curation_context()

        helper.assert_called_once_with(tab._config, audio, subs, offset=4.0)
        assert media_context is sentinel_ctx
        assert lookup_fn is facade_processor.definition_service.lookup_all_offline


class TestUpdateConfig:
    """update_config rebuilds the processor only when idle."""

    def test_update_config_idle_drops_processor_to_none(self, tab, test_config):
        """update_config when idle drops processor to None (lazy-drop, OVH-014).

        No eager rebuild — _start_run will rebuild on the next Mine.
        The old processor must be fully closed (OVH-055, Issue #30).
        """
        old_processor = tab._processor
        new_cfg = replace(test_config, subtitle_offset=2.5)
        with patch(
            "anki_miner.gui.widgets.audiobook_tab.create_episode_processor",
        ) as mock_create:
            tab.update_config(new_cfg)

        assert tab._config is new_cfg
        # Lazy-drop: processor is None; no eager rebuild on the config-refresh path.
        assert tab._processor is None
        mock_create.assert_not_called()
        # Old processor fully closed (dict sqlite + audio Session — OVH-055).
        old_processor.close.assert_called_once()
        old_processor.release_dictionary_resources.assert_not_called()

    def test_update_config_busy_sets_dirty_flag(self, tab, test_config, tmp_path):
        """update_config while a worker runs sets _config_dirty; processor untouched (OVH-056)."""
        _add_pair(tab, tmp_path)
        tab._on_mine_clicked()
        tab.worker_thread.isRunning.return_value = True  # type: ignore[union-attr]
        original_processor = tab._processor

        new_cfg = replace(test_config, subtitle_offset=2.5)
        with patch(
            "anki_miner.gui.widgets.audiobook_tab.create_episode_processor",
        ) as mock_create:
            tab.update_config(new_cfg)

        assert tab._config is new_cfg
        assert tab._processor is original_processor
        # Dirty flag set so _on_worker_finished can reconcile.
        assert tab._config_dirty is True
        # The running processor must NOT have been touched.
        original_processor.close.assert_not_called()
        mock_create.assert_not_called()

    def test_worker_finished_reconciles_dirty_config(self, tab, test_config, tmp_path):
        """_on_worker_finished closes+nulls processor when _config_dirty (OVH-056)."""
        _add_pair(tab, tmp_path)
        tab._on_mine_clicked()
        tab.worker_thread.isRunning.return_value = True
        original_processor = tab._processor

        # Simulate config arriving mid-run.
        new_cfg = replace(test_config, subtitle_offset=2.5)
        tab.update_config(new_cfg)
        assert tab._config_dirty is True

        # Simulate run ending.
        tab.worker_thread.isRunning.return_value = False
        tab._on_worker_finished()

        # Processor closed + nulled; dirty flag cleared.
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

    def test_release_refused_during_run(self, tab, tmp_path):
        _add_pair(tab, tmp_path)
        tab._on_mine_clicked()
        tab.worker_thread.isRunning.return_value = True  # type: ignore[union-attr]

        assert tab.release_dictionary_resources() is False
        assert tab._processor is not None


# ---------------------------------------------------------------------------
# OVH-055 — on-rebuild discard uses close(), not release_dictionary_resources
# ---------------------------------------------------------------------------


class TestUpdateConfigClosesDiscardedProcessor:
    """OVH-055: when update_config rebuilds the processor, the discarded old
    processor must be fully closed (close(), not just release_dictionary_resources())
    so its expression-audio requests.Session is released in addition to dict handles."""

    def test_update_config_calls_close_on_discarded_processor(self, tab, test_config):
        """On idle rebuild, the old processor receives close(), not release_dictionary_resources."""
        old_processor = tab._processor  # MagicMock from fixture

        new_cfg = replace(test_config, subtitle_offset=2.5)
        new_processor = MagicMock(name="NewProcessor")
        with patch(
            "anki_miner.gui.widgets.audiobook_tab.create_episode_processor",
            return_value=new_processor,
        ):
            tab.update_config(new_cfg)

        old_processor.close.assert_called_once()
        old_processor.release_dictionary_resources.assert_not_called()

    def test_update_config_skips_close_when_processor_is_none(self, tab, test_config):
        """If no processor yet (startup-deferred), update_config must not raise."""
        tab._processor = None
        new_cfg = replace(test_config, subtitle_offset=2.5)
        with patch(
            "anki_miner.gui.widgets.audiobook_tab.create_episode_processor",
            return_value=MagicMock(),
        ):
            tab.update_config(new_cfg)  # must not raise
