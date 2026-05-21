"""Tests for the YouTube queue mining tab.

The tab now drives a :class:`YouTubeQueue` instead of a single-URL state
machine. Behaviour under test:

* Add: creates a PROBING item, spawns a probe worker, clears the URL field.
* Probe outcomes: success → READY; failure → PROBE_ERROR.
* Buttons: enabled iff ≥1 READY item exists and no run is active.
* Preview / Mine: instantiates :class:`YouTubeQueueWorker` with the right
  ``preview_mode`` and starts it.
* Stop All: forwards to ``worker.cancel()``.
* Per-item signals (``item_started`` / ``item_progress`` / ``item_finished``)
  update the queue model + row widgets + progress widget.
* ``queue_finished`` clears the worker handle and recomputes buttons.
* ``shutdown()`` cancels the worker and quits all probe workers.
* ``update_config()`` rebuilds fetcher/processor only when no run is active.

Qt threads are never started — ``YouTubeProbeWorker`` and
``YouTubeQueueWorker`` are class-level patched so their ``start()`` is a
no-op and we can inspect constructor arguments.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtWidgets import QApplication

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.youtube_tab import YouTubeTab
from anki_miner.models.youtube import VideoInfo
from anki_miner.models.youtube_queue import YouTubeItemStatus

# QApplication instance needed for any widget test.
_app = QApplication.instance() or QApplication([])


def _make_video_info(
    *,
    video_id: str = "abc123",
    title: str = "Sample Video",
    duration_s: int = 600,
    has_manual_ja_subs: bool = True,
    has_auto_ja_subs: bool = False,
    thumbnail_url: str | None = None,
    uploader: str | None = "Uploader",
    is_live: bool = False,
    is_age_restricted: bool = False,
) -> VideoInfo:
    """Factory for :class:`VideoInfo` with sensible defaults."""
    return VideoInfo(
        video_id=video_id,
        title=title,
        duration_s=duration_s,
        has_manual_ja_subs=has_manual_ja_subs,
        has_auto_ja_subs=has_auto_ja_subs,
        thumbnail_url=thumbnail_url,
        uploader=uploader,
        is_live=is_live,
        is_age_restricted=is_age_restricted,
    )


@pytest.fixture
def tab(test_config: AnkiMinerConfig):
    """Instantiate a YouTubeTab with patched probe/queue worker classes.

    Probe and queue worker classes are patched at the module where the tab
    imports them so their ``start()`` doesn't spawn a real QThread.
    """
    cfg = replace(
        test_config,
        youtube_max_duration_s=7200,
        youtube_cookies_from_browser=None,
    )

    probe_patch = patch("anki_miner.gui.widgets.youtube_tab.YouTubeProbeWorker", autospec=False)
    queue_patch = patch("anki_miner.gui.widgets.youtube_tab.YouTubeQueueWorker", autospec=False)
    with probe_patch as probe_cls, queue_patch as queue_cls:
        # Each instantiation returns a fresh MagicMock with start/cancel/quit/wait stubs.
        probe_cls.side_effect = lambda *a, **kw: MagicMock(name="ProbeWorker")
        queue_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")

        widget = YouTubeTab(
            config=cfg,
            processor=MagicMock(name="EpisodeProcessor"),
            fetcher=MagicMock(name="Fetcher"),
            presenter=MagicMock(name="Presenter"),
        )
        widget._probe_worker_cls = probe_cls  # type: ignore[attr-defined]
        widget._queue_worker_cls = queue_cls  # type: ignore[attr-defined]
        try:
            yield widget
        finally:
            widget.deleteLater()


def _add_ready_item(tab, url: str = "https://www.youtube.com/watch?v=abc", **probe_kwargs):
    """Helper: add URL, simulate successful probe, return the item."""
    tab.url_edit.setText(url)
    tab._on_add_clicked()
    item = tab._queue.all_items()[-1]
    info = _make_video_info(**probe_kwargs)
    tab._on_probe_done(item, info)
    return item


class TestInitialState:
    """Empty queue: Add enabled, all action buttons disabled."""

    def test_empty_queue_buttons(self, tab):
        assert tab._queue.all_items() == []
        assert tab.add_button.isEnabled()
        assert not tab.preview_button.isEnabled()
        assert not tab.mine_button.isEnabled()
        assert not tab.clear_button.isEnabled()
        assert tab.stop_button.isHidden()
        assert tab.worker_thread is None

    def test_list_widget_empty(self, tab):
        assert tab.list_widget.count() == 0


class TestAddUrl:
    """Add button spawns probe + creates row in PROBING state."""

    def test_add_creates_probing_item(self, tab):
        tab.url_edit.setText("https://youtu.be/abc123")
        tab._on_add_clicked()

        items = tab._queue.all_items()
        assert len(items) == 1
        assert items[0].url == "https://youtu.be/abc123"
        assert items[0].status == YouTubeItemStatus.PROBING

    def test_add_clears_url_field(self, tab):
        tab.url_edit.setText("https://youtu.be/abc123")
        tab._on_add_clicked()
        assert tab.url_edit.text() == ""

    def test_add_empty_url_noop(self, tab):
        tab.url_edit.setText("   ")
        tab._on_add_clicked()
        assert tab._queue.all_items() == []

    def test_add_spawns_probe_worker(self, tab):
        probe_cls = tab._probe_worker_cls  # patched
        tab.url_edit.setText("https://youtu.be/abc123")
        tab._on_add_clicked()
        assert probe_cls.call_count == 1
        # Probe instance kept alive in tab's list.
        assert len(tab._probe_workers) == 1

    def test_add_renders_row_widget(self, tab):
        tab.url_edit.setText("https://youtu.be/abc123")
        tab._on_add_clicked()
        assert tab.list_widget.count() == 1
        item = tab._queue.all_items()[0]
        assert item in tab._row_widgets

    def test_multiple_adds_parallel_probes(self, tab):
        probe_cls = tab._probe_worker_cls
        for i in range(3):
            tab.url_edit.setText(f"https://youtu.be/v{i}")
            tab._on_add_clicked()
        assert probe_cls.call_count == 3
        assert len(tab._probe_workers) == 3


class TestProbeOutcomes:
    """Probe done/error flip the item's status and refresh buttons."""

    def test_probe_done_flips_to_ready_enables_buttons(self, tab):
        tab.url_edit.setText("https://youtu.be/abc")
        tab._on_add_clicked()
        item = tab._queue.all_items()[-1]

        tab._on_probe_done(item, _make_video_info())

        assert item.status == YouTubeItemStatus.READY
        assert item.video_info is not None
        assert item.video_id == "abc123"
        assert item.resolved_sub_mode == "manual_only"
        assert tab.preview_button.isEnabled()
        assert tab.mine_button.isEnabled()
        assert tab.clear_button.isEnabled()

    def test_probe_done_auto_only(self, tab):
        tab.url_edit.setText("https://youtu.be/abc")
        tab._on_add_clicked()
        item = tab._queue.all_items()[-1]

        tab._on_probe_done(item, _make_video_info(has_manual_ja_subs=False, has_auto_ja_subs=True))

        assert item.status == YouTubeItemStatus.READY
        assert item.resolved_sub_mode == "auto_only"

    def test_probe_done_live_marks_probe_error(self, tab):
        tab.url_edit.setText("https://youtu.be/abc")
        tab._on_add_clicked()
        item = tab._queue.all_items()[-1]

        tab._on_probe_done(item, _make_video_info(is_live=True))

        assert item.status == YouTubeItemStatus.PROBE_ERROR
        assert "live" in (item.error_message or "").lower()
        assert not tab.preview_button.isEnabled()
        assert not tab.mine_button.isEnabled()

    def test_probe_done_too_long_marks_probe_error(self, tab):
        tab.url_edit.setText("https://youtu.be/abc")
        tab._on_add_clicked()
        item = tab._queue.all_items()[-1]

        tab._on_probe_done(
            item,
            _make_video_info(duration_s=tab._config.youtube_max_duration_s + 1),
        )

        assert item.status == YouTubeItemStatus.PROBE_ERROR
        assert not tab.preview_button.isEnabled()

    def test_probe_done_age_locked_without_cookies(self, tab):
        tab.url_edit.setText("https://youtu.be/abc")
        tab._on_add_clicked()
        item = tab._queue.all_items()[-1]

        tab._on_probe_done(item, _make_video_info(is_age_restricted=True))

        assert item.status == YouTubeItemStatus.PROBE_ERROR
        assert "age" in (item.error_message or "").lower()

    def test_probe_done_no_subs_marks_probe_error(self, tab):
        tab.url_edit.setText("https://youtu.be/abc")
        tab._on_add_clicked()
        item = tab._queue.all_items()[-1]

        tab._on_probe_done(item, _make_video_info(has_manual_ja_subs=False, has_auto_ja_subs=False))

        assert item.status == YouTubeItemStatus.PROBE_ERROR

    def test_probe_error_flips_to_probe_error(self, tab):
        tab.url_edit.setText("https://youtu.be/abc")
        tab._on_add_clicked()
        item = tab._queue.all_items()[-1]

        tab._on_probe_error(item, "yt-dlp exploded")

        assert item.status == YouTubeItemStatus.PROBE_ERROR
        assert item.error_message == "yt-dlp exploded"
        assert not tab.preview_button.isEnabled()
        assert not tab.mine_button.isEnabled()

    def test_probe_error_with_ready_sibling_keeps_buttons_enabled(self, tab):
        # First item ready
        _add_ready_item(tab, "https://youtu.be/ok")

        # Second item errors
        tab.url_edit.setText("https://youtu.be/bad")
        tab._on_add_clicked()
        bad = tab._queue.all_items()[-1]
        tab._on_probe_error(bad, "nope")

        assert tab.preview_button.isEnabled()
        assert tab.mine_button.isEnabled()


class TestRunStartup:
    """Preview / Mine buttons construct the queue worker correctly."""

    def test_mine_constructs_queue_worker_preview_false(self, tab):
        _add_ready_item(tab)
        queue_cls = tab._queue_worker_cls
        tab._on_mine_clicked()

        assert queue_cls.call_count == 1
        kwargs = queue_cls.call_args.kwargs
        assert kwargs["preview_mode"] is False
        assert kwargs["processor"] is tab._processor
        assert kwargs["config"] is tab._config
        # Curation callback always passed; worker decides per item.
        assert kwargs["curation_callback"] == tab._curation_bridge
        # Worker handle set.
        assert tab.worker_thread is not None

    def test_preview_constructs_queue_worker_preview_true(self, tab):
        _add_ready_item(tab)
        queue_cls = tab._queue_worker_cls
        tab._on_preview_clicked()

        kwargs = queue_cls.call_args.kwargs
        assert kwargs["preview_mode"] is True

    def test_mine_passes_ready_items_only(self, tab):
        _add_ready_item(tab, "https://youtu.be/v1")
        # An item still PROBING should NOT reach the worker.
        tab.url_edit.setText("https://youtu.be/v2")
        tab._on_add_clicked()  # PROBING

        queue_cls = tab._queue_worker_cls
        tab._on_mine_clicked()

        kwargs = queue_cls.call_args.kwargs
        items = kwargs["items"]
        assert len(items) == 1
        assert items[0].url == "https://youtu.be/v1"

    def test_mine_with_no_ready_items_noop(self, tab):
        queue_cls = tab._queue_worker_cls
        tab._on_mine_clicked()
        assert queue_cls.call_count == 0
        assert tab.worker_thread is None

    def test_run_active_disables_action_buttons(self, tab):
        _add_ready_item(tab)
        tab._on_mine_clicked()

        assert not tab.add_button.isEnabled()
        assert not tab.preview_button.isEnabled()
        assert not tab.mine_button.isEnabled()
        assert not tab.stop_button.isHidden()


class TestStopAll:
    """Stop All forwards to the worker's cancel()."""

    def test_stop_all_calls_worker_cancel(self, tab):
        _add_ready_item(tab)
        tab._on_mine_clicked()
        worker = tab.worker_thread

        tab._on_stop_all_clicked()

        worker.cancel.assert_called_once()  # type: ignore[union-attr]

    def test_stop_all_noop_when_no_worker(self, tab):
        # Should not raise.
        tab._on_stop_all_clicked()


class TestPerItemSignals:
    """Per-item signals update the row widgets and progress widget."""

    def test_item_started_marks_processing(self, tab):
        item_a = _add_ready_item(tab, "https://youtu.be/v1", video_id="aaa")
        _add_ready_item(tab, "https://youtu.be/v2", video_id="bbb")
        _add_ready_item(tab, "https://youtu.be/v3", video_id="ccc")
        tab._on_mine_clicked()

        tab._on_item_started(0)

        assert item_a.status == YouTubeItemStatus.PROCESSING
        # Progress widget shows "Mining 1 of 3: Sample Video" — total drawn from
        # the run snapshot, not the live queue, so it never shows "1 of 1".
        assert "Mining 1 of 3" in tab.progress_widget.status_label.text()
        assert "Sample Video" in tab.progress_widget.status_label.text()

    def test_item_progress_determinate(self, tab):
        item = _add_ready_item(tab)
        tab._on_mine_clicked()
        tab._on_item_started(0)

        tab._on_item_progress(0, "Downloading", 42)

        assert tab.progress_widget.progress_bar.maximum() == 100
        assert tab.progress_widget.progress_bar.value() == 42
        assert "Downloading" in tab.progress_widget.status_label.text()
        assert item.status == YouTubeItemStatus.PROCESSING

    def test_item_progress_indeterminate(self, tab):
        _add_ready_item(tab)
        tab._on_mine_clicked()
        tab._on_item_started(0)

        tab._on_item_progress(0, "Merging", -1)
        assert tab.progress_widget.progress_bar.maximum() == 0  # indeterminate
        assert "Merging" in tab.progress_widget.status_label.text()

    def test_item_finished_success_marks_completed(self, tab):
        item = _add_ready_item(tab)
        tab._on_mine_clicked()
        tab._on_item_started(0)

        result = MagicMock(cards_created=5)
        tab._on_item_finished(0, result, None, 1)

        assert item.status == YouTubeItemStatus.COMPLETED
        assert item.cards_created == 5
        # Presenter is forwarded the result.
        tab._presenter.show_processing_result.assert_called_once_with(result)

    def test_item_finished_error_marks_error(self, tab):
        item = _add_ready_item(tab)
        tab._on_mine_clicked()
        tab._on_item_started(0)

        tab._on_item_finished(0, None, "FetchError: oops", 2)

        assert item.status == YouTubeItemStatus.ERROR
        assert item.error_message == "FetchError: oops"

    def test_item_finished_presenter_error_swallowed(self, tab):
        item = _add_ready_item(tab)
        tab._on_mine_clicked()
        tab._on_item_started(0)

        tab._presenter.show_processing_result.side_effect = RuntimeError("presenter blew up")

        # Must not propagate.
        tab._on_item_finished(0, MagicMock(cards_created=1), None, 1)
        assert item.status == YouTubeItemStatus.COMPLETED


class TestQueueFinished:
    """``queue_finished`` clears worker, restores buttons, logs summary."""

    def test_queue_finished_clears_worker(self, tab):
        _add_ready_item(tab)
        tab._on_mine_clicked()
        assert tab.worker_thread is not None

        tab._on_queue_finished()

        assert tab.worker_thread is None

    def test_queue_finished_recomputes_buttons(self, tab):
        item = _add_ready_item(tab)
        tab._on_mine_clicked()

        # Mark item complete to keep it READY-like (no remaining mineable items).
        tab._on_item_started(0)
        tab._on_item_finished(0, MagicMock(cards_created=2), None, 1)
        tab._on_queue_finished()

        # No more READY items; Preview/Mine disabled, Add re-enabled, Stop hidden.
        assert tab.add_button.isEnabled()
        assert not tab.preview_button.isEnabled()
        assert not tab.mine_button.isEnabled()
        assert tab.stop_button.isHidden()
        # Item record preserved.
        assert item.status == YouTubeItemStatus.COMPLETED

    def test_queue_finished_summary_logged(self, tab):
        _add_ready_item(tab, "https://youtu.be/ok")
        _add_ready_item(tab, "https://youtu.be/bad")
        tab._on_mine_clicked()
        tab._on_item_started(0)
        tab._on_item_finished(0, MagicMock(cards_created=2), None, 1)
        tab._on_item_started(1)
        tab._on_item_finished(1, None, "boom", 2)
        tab._on_queue_finished()

        # Last log line should mention 1 succeeded, 1 failed.
        text = tab.log_widget.text_edit.toPlainText()
        assert "1 succeeded" in text
        assert "1 failed" in text


class TestRemoveAndClear:
    """Remove button and Clear button manage queue contents."""

    def test_remove_item(self, tab):
        item = _add_ready_item(tab, "https://youtu.be/v1")
        _add_ready_item(tab, "https://youtu.be/v2")
        assert len(tab._queue.all_items()) == 2

        tab._on_remove_clicked(item)

        urls = [i.url for i in tab._queue.all_items()]
        assert urls == ["https://youtu.be/v2"]
        assert tab.list_widget.count() == 1
        assert item not in tab._row_widgets

    def test_clear_removes_non_processing(self, tab):
        _add_ready_item(tab, "https://youtu.be/v1")
        _add_ready_item(tab, "https://youtu.be/v2")

        tab._on_clear_clicked()

        assert tab._queue.all_items() == []
        assert tab.list_widget.count() == 0

    def test_clear_during_run_preserves_processing(self, tab):
        item1 = _add_ready_item(tab, "https://youtu.be/v1")
        _add_ready_item(tab, "https://youtu.be/v2")
        tab._on_mine_clicked()
        tab._on_item_started(0)  # item1 -> PROCESSING

        tab._on_clear_clicked()

        remaining = tab._queue.all_items()
        assert remaining == [item1]
        assert tab.list_widget.count() == 1


class TestShutdown:
    """shutdown() cancels worker + cleans up probe workers."""

    def test_shutdown_with_active_worker(self, tab):
        _add_ready_item(tab)
        tab._on_mine_clicked()
        worker = tab.worker_thread

        tab.shutdown()

        worker.cancel.assert_called_once()  # type: ignore[union-attr]
        worker.wait.assert_called()  # type: ignore[union-attr]
        assert tab.worker_thread is None

    def test_shutdown_cleans_probe_workers(self, tab):
        tab.url_edit.setText("https://youtu.be/v1")
        tab._on_add_clicked()
        tab.url_edit.setText("https://youtu.be/v2")
        tab._on_add_clicked()

        probes = list(tab._probe_workers)
        assert len(probes) == 2

        tab.shutdown()

        for p in probes:
            p.quit.assert_called()
            p.wait.assert_called()
        assert tab._probe_workers == []

    def test_shutdown_with_nothing_active(self, tab):
        # Should not raise.
        tab.shutdown()


class TestIdxSnapshotBug:
    """Regression: removing a COMPLETED row mid-run must not shift the idx mapping."""

    def test_idx_resolution_survives_completed_item_removal_during_run(self, tab):
        """Removing a COMPLETED item mid-run must not shift idx mapping for surviving items."""
        # Add three items and probe them all to READY.
        item_a = _add_ready_item(tab, "https://youtu.be/v1", video_id="aaa")
        item_b = _add_ready_item(tab, "https://youtu.be/v2", video_id="bbb")
        item_c = _add_ready_item(tab, "https://youtu.be/v3", video_id="ccc")

        tab._on_mine_clicked()

        # Simulate item 0 (A) starting and finishing.
        tab._on_item_started(0)
        assert item_a.status == YouTubeItemStatus.PROCESSING
        tab._on_item_finished(0, MagicMock(cards_created=2), None, 1)
        assert item_a.status == YouTubeItemStatus.COMPLETED

        # User removes the COMPLETED row for A while the run is still in flight.
        tab._on_remove_clicked(item_a)
        # A is gone from the live queue…
        assert item_a not in tab._queue.all_items()
        # …but _run_items snapshot still holds all three in order.
        assert tab._run_items == [item_a, item_b, item_c]

        # Worker fires item_started(1) — must land on B, not C.
        tab._on_item_started(1)
        assert item_b.status == YouTubeItemStatus.PROCESSING, (
            "item_b should be PROCESSING after item_started(1); "
            "a live-queue lookup would have resolved to item_c instead"
        )
        assert item_c.status == YouTubeItemStatus.READY

        # Worker finishes item 1 — B becomes COMPLETED; C unchanged.
        tab._on_item_finished(1, MagicMock(cards_created=1), None, 1)
        assert item_b.status == YouTubeItemStatus.COMPLETED
        assert item_c.status == YouTubeItemStatus.READY

        # Worker fires item_started(2) — must land on C.
        tab._on_item_started(2)
        assert item_c.status == YouTubeItemStatus.PROCESSING

    def test_run_items_cleared_after_queue_finished(self, tab):
        """_run_items is reset to [] when the queue worker finishes."""
        _add_ready_item(tab, "https://youtu.be/v1")
        tab._on_mine_clicked()
        assert len(tab._run_items) == 1

        tab._on_queue_finished()
        assert tab._run_items == []

    def test_mining_total_uses_snapshot_not_live_queue(self, tab):
        """'Mining X of Y' total reflects the run snapshot even if items were removed."""
        item_a = _add_ready_item(tab, "https://youtu.be/v1", video_id="aaa")
        item_b = _add_ready_item(tab, "https://youtu.be/v2", video_id="bbb")
        _add_ready_item(tab, "https://youtu.be/v3", video_id="ccc")
        tab._on_mine_clicked()

        # Simulate A finishing and its row being removed.
        tab._on_item_started(0)
        tab._on_item_finished(0, MagicMock(cards_created=1), None, 1)
        tab._on_remove_clicked(item_a)

        # Now B starts — total should still be 3 (from snapshot), not 2 (live).
        tab._on_item_started(1)
        status_text = tab.progress_widget.status_label.text()
        assert "of 3" in status_text, f"Expected 'of 3' in status text but got: {status_text!r}"
        assert item_b.status == YouTubeItemStatus.PROCESSING


class TestUpdateConfig:
    """update_config rebuilds services but never mid-run."""

    def test_update_config_rebuilds_when_idle(self, tab, test_config):
        new_cfg = replace(test_config, youtube_max_duration_s=999)
        new_fetcher = MagicMock(name="NewFetcher")
        new_processor = MagicMock(name="NewProcessor")
        with (
            patch("anki_miner.gui.widgets.youtube_tab.create_youtube_fetcher", return_value=new_fetcher),
            patch("anki_miner.gui.widgets.youtube_tab.create_episode_processor", return_value=new_processor),
        ):
            tab.update_config(new_cfg)

        assert tab._config is new_cfg
        assert tab._fetcher is new_fetcher
        assert tab._processor is new_processor

    def test_update_config_skips_processor_rebuild_during_run(self, tab, test_config):
        _add_ready_item(tab)
        tab._on_mine_clicked()
        worker = tab.worker_thread
        # Worker is a MagicMock; configure isRunning() to return True.
        worker.isRunning.return_value = True  # type: ignore[union-attr]
        original_processor = tab._processor

        new_cfg = replace(test_config, youtube_max_duration_s=999)
        new_fetcher = MagicMock(name="NewFetcher")
        new_processor = MagicMock(name="NewProcessor")
        with (
            patch("anki_miner.gui.widgets.youtube_tab.create_youtube_fetcher", return_value=new_fetcher),
            patch("anki_miner.gui.widgets.youtube_tab.create_episode_processor", return_value=new_processor),
        ):
            tab.update_config(new_cfg)

        # Fetcher always rebuilt; processor preserved because the worker is busy.
        assert tab._fetcher is new_fetcher
        assert tab._processor is original_processor
