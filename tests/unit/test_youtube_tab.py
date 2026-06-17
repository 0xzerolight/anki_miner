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
* Playlist URLs (Issue #70): Add spawns a resolve worker, the resolved
  playlist expands into PROBING rows (deduped), and per-entry probe signals
  reuse the single-video classification path.

Qt threads are never started — ``YouTubeProbeWorker``, ``YouTubeQueueWorker``,
``YouTubePlaylistResolveWorker``, and ``YouTubePlaylistProbeWorker`` are
class-level patched so their ``start()`` is a no-op and we can inspect
constructor arguments. The add flow (probe + playlist workers, generation
counter, choice dialog) lives on ``tab._add_flow``, a
:class:`~anki_miner.gui.widgets.youtube_playlist_flow.PlaylistAddController`;
those worker classes are therefore patched at ``youtube_playlist_flow``,
while ``YouTubeQueueWorker`` (run flow) stays patched at ``youtube_tab``.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.youtube_tab import YouTubeTab
from anki_miner.models.youtube import PlaylistEntry, PlaylistInfo, VideoInfo
from anki_miner.models.youtube_queue import YouTubeItemStatus
from anki_miner.utils.youtube_url import classify_youtube_url


def _make_video_info(
    *,
    video_id: str = "abc123",
    title: str = "Sample Video",
    duration_s: int = 600,
    has_manual_ja_subs: bool = True,
    has_auto_ja_subs: bool = False,
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
        is_live=is_live,
        is_age_restricted=is_age_restricted,
    )


def _make_playlist_entry(
    *,
    video_id: str,
    title: str = "Playlist Video",
    duration_s: int | None = 120,
) -> PlaylistEntry:
    """Factory for :class:`PlaylistEntry` with a canonical watch URL."""
    return PlaylistEntry(
        video_id=video_id,
        title=title,
        duration_s=duration_s,
        url=f"https://www.youtube.com/watch?v={video_id}",
    )


def _make_playlist_info(
    *,
    n: int = 3,
    playlist_id: str | None = "PLabcdefghijkl",
    title: str = "My Playlist",
    total_count: int | None = None,
) -> PlaylistInfo:
    """Factory for :class:`PlaylistInfo` with *n* sequential entries."""
    entries = tuple(_make_playlist_entry(video_id=f"vid{i:08d}", title=f"Video {i}") for i in range(n))
    return PlaylistInfo(playlist_id=playlist_id, title=title, entries=entries, total_count=total_count)


PLAYLIST_URL = "https://www.youtube.com/playlist?list=PLabcdefghijkl"
MIXED_URL = "https://www.youtube.com/watch?v=abcdefghijk&list=PLabcdefghijkl"


@pytest.fixture
def tab(qtbot, test_config: AnkiMinerConfig):
    """Instantiate a YouTubeTab with patched probe/queue/playlist worker classes.

    Worker classes are patched at the module where they are looked up — the
    probe/playlist workers on ``youtube_playlist_flow`` (the add-flow
    controller), the queue worker on ``youtube_tab`` — so their ``start()``
    doesn't spawn a real QThread.
    """
    cfg = replace(
        test_config,
        youtube_max_duration_s=7200,
        youtube_cookies_from_browser=None,
    )

    probe_patch = patch("anki_miner.gui.widgets.youtube_playlist_flow.YouTubeProbeWorker", autospec=False)
    queue_patch = patch("anki_miner.gui.widgets.youtube_tab.YouTubeQueueWorker", autospec=False)
    resolve_patch = patch("anki_miner.gui.widgets.youtube_playlist_flow.YouTubePlaylistResolveWorker", autospec=False)
    pl_probe_patch = patch("anki_miner.gui.widgets.youtube_playlist_flow.YouTubePlaylistProbeWorker", autospec=False)
    with (
        probe_patch as probe_cls,
        queue_patch as queue_cls,
        resolve_patch as resolve_cls,
        pl_probe_patch as pl_probe_cls,
    ):
        # Each instantiation returns a fresh MagicMock with start/cancel/quit/wait stubs.
        probe_cls.side_effect = lambda *a, **kw: MagicMock(name="ProbeWorker")
        queue_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")
        resolve_cls.side_effect = lambda *a, **kw: MagicMock(name="PlaylistResolveWorker")
        pl_probe_cls.side_effect = lambda *a, **kw: MagicMock(name="PlaylistProbeWorker")

        widget = YouTubeTab(
            config=cfg,
            processor=MagicMock(name="EpisodeProcessor"),
            fetcher=MagicMock(name="Fetcher"),
            presenter=MagicMock(name="Presenter"),
        )
        qtbot.addWidget(widget)
        widget._probe_worker_cls = probe_cls  # type: ignore[attr-defined]
        widget._queue_worker_cls = queue_cls  # type: ignore[attr-defined]
        widget._playlist_resolve_worker_cls = resolve_cls  # type: ignore[attr-defined]
        widget._playlist_probe_worker_cls = pl_probe_cls  # type: ignore[attr-defined]
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
    tab._add_flow._on_probe_done(item, info)
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
        assert len(tab._add_flow._probe_workers) == 1

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
        assert len(tab._add_flow._probe_workers) == 3


class TestAddUrlRejection:
    """Add rejects inputs that aren't http(s) / a YouTube URL / a video id (T-34).

    Guards against an option-leading "URL" reaching yt-dlp as an argument
    (e.g. ``--update-to=...`` -> attacker-repo self-replacement on the probe).
    A rejected input must queue nothing, spawn no probe, and surface a
    user-visible error.
    """

    def test_option_leading_url_rejected(self, tab):
        probe_cls = tab._probe_worker_cls
        tab.url_edit.setText("--update-to=evil/fork@tag")
        tab._on_add_clicked()

        assert tab._queue.all_items() == []
        assert probe_cls.call_count == 0
        assert len(tab._add_flow._probe_workers) == 0
        # User-visible feedback and the URL field is NOT cleared (so the user
        # can see/fix what they pasted).
        assert "valid" in tab.log_widget.text_edit.toPlainText().lower()
        assert tab.url_edit.text() == "--update-to=evil/fork@tag"

    def test_dash_config_location_rejected(self, tab):
        probe_cls = tab._probe_worker_cls
        tab.url_edit.setText("--config-location=/tmp/evil.conf")
        tab._on_add_clicked()
        assert tab._queue.all_items() == []
        assert probe_cls.call_count == 0

    def test_plain_https_url_still_accepted(self, tab):
        # http(s) inputs remain accepted — yt-dlp stays the final validator
        # for non-YouTube-shaped URLs (no behaviour change for that path).
        probe_cls = tab._probe_worker_cls
        tab.url_edit.setText("https://example.com/whatever")
        tab._on_add_clicked()
        assert len(tab._queue.all_items()) == 1
        assert probe_cls.call_count == 1

    def test_bare_video_id_accepted(self, tab):
        probe_cls = tab._probe_worker_cls
        tab.url_edit.setText("dQw4w9WgXcQ")
        tab._on_add_clicked()
        assert len(tab._queue.all_items()) == 1
        assert probe_cls.call_count == 1

    def test_bare_video_id_normalised_to_watch_url(self, tab):
        """OVH-036: bare id must be normalised to a canonical watch URL so that
        the item classifies correctly in playlist dedup (classify_youtube_url)."""
        tab.url_edit.setText("dQw4w9WgXcQ")
        tab._on_add_clicked()
        item = tab._queue.all_items()[-1]
        assert item.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


class TestProbeOutcomes:
    """Probe done/error flip the item's status and refresh buttons."""

    def test_probe_done_flips_to_ready_enables_buttons(self, tab):
        tab.url_edit.setText("https://youtu.be/abc")
        tab._on_add_clicked()
        item = tab._queue.all_items()[-1]

        tab._add_flow._on_probe_done(item, _make_video_info())

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

        tab._add_flow._on_probe_done(item, _make_video_info(has_manual_ja_subs=False, has_auto_ja_subs=True))

        assert item.status == YouTubeItemStatus.READY
        assert item.resolved_sub_mode == "auto_only"

    def test_probe_done_live_marks_probe_error(self, tab):
        tab.url_edit.setText("https://youtu.be/abc")
        tab._on_add_clicked()
        item = tab._queue.all_items()[-1]

        tab._add_flow._on_probe_done(item, _make_video_info(is_live=True))

        assert item.status == YouTubeItemStatus.PROBE_ERROR
        assert "live" in (item.error_message or "").lower()
        assert not tab.preview_button.isEnabled()
        assert not tab.mine_button.isEnabled()

    def test_probe_done_too_long_marks_probe_error(self, tab):
        tab.url_edit.setText("https://youtu.be/abc")
        tab._on_add_clicked()
        item = tab._queue.all_items()[-1]

        tab._add_flow._on_probe_done(
            item,
            _make_video_info(duration_s=tab._config.youtube_max_duration_s + 1),
        )

        assert item.status == YouTubeItemStatus.PROBE_ERROR
        assert not tab.preview_button.isEnabled()

    def test_probe_done_age_locked_without_cookies(self, tab):
        tab.url_edit.setText("https://youtu.be/abc")
        tab._on_add_clicked()
        item = tab._queue.all_items()[-1]

        tab._add_flow._on_probe_done(item, _make_video_info(is_age_restricted=True))

        assert item.status == YouTubeItemStatus.PROBE_ERROR
        assert "age" in (item.error_message or "").lower()

    def test_probe_done_no_subs_marks_probe_error(self, tab):
        tab.url_edit.setText("https://youtu.be/abc")
        tab._on_add_clicked()
        item = tab._queue.all_items()[-1]

        tab._add_flow._on_probe_done(item, _make_video_info(has_manual_ja_subs=False, has_auto_ja_subs=False))

        assert item.status == YouTubeItemStatus.PROBE_ERROR

    def test_probe_error_flips_to_probe_error(self, tab):
        tab.url_edit.setText("https://youtu.be/abc")
        tab._on_add_clicked()
        item = tab._queue.all_items()[-1]

        tab._add_flow._on_probe_error(item, "yt-dlp exploded")

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
        tab._add_flow._on_probe_error(bad, "nope")

        assert tab.preview_button.isEnabled()
        assert tab.mine_button.isEnabled()


class TestDeferredProcessor:
    """Startup-deferral contract: tab accepts ``processor=None`` and rebuilds
    lazily via service_factory, threading ``stats_service`` through so YouTube
    mining sessions land in analytics.
    """

    def test_constructs_with_none_processor(self, qtbot, test_config: AnkiMinerConfig):
        cfg = replace(test_config, youtube_max_duration_s=7200)
        sentinel = MagicMock(name="StatsService")
        with (
            patch("anki_miner.gui.widgets.youtube_playlist_flow.YouTubeProbeWorker", autospec=False),
            patch("anki_miner.gui.widgets.youtube_tab.YouTubeQueueWorker", autospec=False),
        ):
            widget = YouTubeTab(
                config=cfg,
                processor=None,
                fetcher=MagicMock(name="Fetcher"),
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
        cfg = replace(test_config, youtube_max_duration_s=7200)
        sentinel_stats = MagicMock(name="StatsService")
        with (
            patch("anki_miner.gui.widgets.youtube_playlist_flow.YouTubeProbeWorker", autospec=False),
            patch("anki_miner.gui.widgets.youtube_tab.YouTubeQueueWorker", autospec=False) as q_cls,
            patch(
                "anki_miner.gui.widgets.youtube_tab.create_episode_processor",
            ) as mock_create,
        ):
            q_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")
            built_processor = MagicMock(name="LazyProcessor")
            mock_create.return_value = built_processor

            widget = YouTubeTab(
                config=cfg,
                processor=None,
                fetcher=MagicMock(name="Fetcher"),
                presenter=MagicMock(name="Presenter"),
                stats_service=sentinel_stats,
            )
            qtbot.addWidget(widget)
            try:
                # Drive _start_run via the public Mine path: add a ready item,
                # click Mine, and assert the lazy rebuild happened with stats.
                widget.url_edit.setText("https://youtu.be/abc")
                widget._on_add_clicked()
                item = widget._queue.all_items()[-1]
                widget._add_flow._on_probe_done(item, _make_video_info())
                widget._on_mine_clicked()

                assert mock_create.call_count == 1
                kwargs = mock_create.call_args.kwargs
                assert kwargs["stats_service"] is sentinel_stats
                assert widget._processor is built_processor
            finally:
                widget.deleteLater()

    def test_update_config_on_deferred_processor_keeps_stats_service(self, qtbot, test_config: AnkiMinerConfig):
        """update_config before the first run must not drop stats_service (T-15).

        When the processor is still None (startup-deferred), the rebuild used
        ``getattr(None, "stats_service", None)`` -> None, silently disabling
        stats.db recording for the session. The tab's own stats service must
        be threaded through instead.
        """
        cfg = replace(test_config, youtube_max_duration_s=7200)
        sentinel_stats = MagicMock(name="StatsService")
        with (
            patch("anki_miner.gui.widgets.youtube_playlist_flow.YouTubeProbeWorker", autospec=False),
            patch("anki_miner.gui.widgets.youtube_tab.YouTubeQueueWorker", autospec=False),
            patch("anki_miner.gui.widgets.youtube_tab.create_youtube_fetcher", return_value=MagicMock()),
            patch("anki_miner.gui.widgets.youtube_tab.create_episode_processor") as mock_create,
        ):
            mock_create.return_value = MagicMock(name="RebuiltProcessor")
            widget = YouTubeTab(
                config=cfg,
                processor=None,
                fetcher=MagicMock(name="Fetcher"),
                presenter=MagicMock(name="Presenter"),
                stats_service=sentinel_stats,
            )
            qtbot.addWidget(widget)
            try:
                widget.update_config(replace(cfg, youtube_max_duration_s=999))

                assert mock_create.call_count == 1
                assert mock_create.call_args.kwargs["stats_service"] is sentinel_stats
            finally:
                widget.deleteLater()


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
        # Curation callback gated by the (default-unchecked) review checkbox.
        assert kwargs["curation_callback"] is None
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
    """``queue_finished`` (success-path-only) logs the run summary.

    Worker state cleanup lives in :class:`TestWorkerFinished` because it must
    run on every exit path, not just the success path.
    """

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

    def test_queue_finished_does_not_mutate_state(self, tab):
        """``_on_queue_finished`` only logs — state cleanup is wired to ``QThread.finished``."""
        _add_ready_item(tab)
        tab._on_mine_clicked()
        worker = tab.worker_thread
        assert worker is not None
        assert tab._run_items != []

        tab._on_queue_finished()

        # No state mutation: cleanup is the job of ``_on_worker_finished``.
        assert tab.worker_thread is worker
        assert tab._run_items != []


class TestWorkerFinished:
    """``QThread.finished`` is the single cleanup signal for every run-exit path."""

    def test_worker_finished_clears_worker(self, tab):
        _add_ready_item(tab)
        tab._on_mine_clicked()
        assert tab.worker_thread is not None

        tab._on_worker_finished()

        assert tab.worker_thread is None
        assert tab._run_items == []

    def test_worker_finished_recomputes_buttons(self, tab):
        item = _add_ready_item(tab)
        tab._on_mine_clicked()
        tab._on_item_started(0)
        tab._on_item_finished(0, MagicMock(cards_created=2), None, 1)
        tab._on_queue_finished()
        tab._on_worker_finished()

        # No more READY items; Preview/Mine disabled, Add re-enabled, Stop hidden.
        assert tab.add_button.isEnabled()
        assert not tab.preview_button.isEnabled()
        assert not tab.mine_button.isEnabled()
        assert tab.stop_button.isHidden()
        # Item record preserved.
        assert item.status == YouTubeItemStatus.COMPLETED

    def test_worker_finished_restores_stop_button(self, tab):
        _add_ready_item(tab)
        tab._on_mine_clicked()
        # Simulate Stop All click mid-run.
        tab._on_stop_all_clicked()
        assert tab.stop_button.text() == "Cancelling…"
        assert not tab.stop_button.isEnabled()

        tab._on_worker_finished()

        assert tab.stop_button.text() == "Stop All"
        assert tab.stop_button.isEnabled()

    def test_worker_finished_resets_progress_after_merging(self, tab):
        """Regression: progress bar left on ``Merging`` (indeterminate) gets reset on run end."""
        _add_ready_item(tab)
        tab._on_mine_clicked()
        tab._on_item_started(0)
        # Simulate the fetcher's final indeterminate emit — last signal before
        # the mining pipeline short-circuits on a zero-unknown-word preview.
        tab._on_item_progress(0, "Merging", -1)
        assert tab.progress_widget.progress_bar.maximum() == 0  # indeterminate
        assert "Merging" in tab.progress_widget.status_label.text()

        tab._on_queue_finished()
        tab._on_worker_finished()

        assert tab.progress_widget.progress_bar.maximum() == 100
        assert tab.progress_widget.progress_bar.value() == 0
        assert tab.progress_widget.status_label.text() == "Ready"

    def test_worker_finished_recovers_from_cancel_without_queue_finished(self, tab):
        """Mid-fetch cancel skips ``queue_finished`` — ``finished`` must still clean up."""
        _add_ready_item(tab)
        tab._on_mine_clicked()
        tab._on_item_started(0)
        tab._on_item_progress(0, "Merging", -1)
        tab._on_stop_all_clicked()
        # Note: no _on_queue_finished — worker.run() returned early on cancel.

        tab._on_worker_finished()

        assert tab.worker_thread is None
        assert tab._run_items == []
        assert tab.stop_button.text() == "Stop All"
        assert tab.stop_button.isEnabled()
        assert tab.progress_widget.progress_bar.maximum() == 100
        assert tab.progress_widget.status_label.text() == "Ready"


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

    def test_clear_during_run_skips_dropped_items_in_worker(self, tab):
        """Mid-run Clear must reach the worker, not just the GUI model (T-23).

        The worker iterates its constructor snapshot, so dropping items from
        the tab's queue alone still mined them — cards for rows that no
        longer existed.
        """
        item1 = _add_ready_item(tab, "https://youtu.be/v1")
        item2 = _add_ready_item(tab, "https://youtu.be/v2")
        item3 = _add_ready_item(tab, "https://youtu.be/v3")
        tab._on_mine_clicked()
        tab._on_item_started(0)  # item1 -> PROCESSING
        worker = tab.worker_thread

        tab._on_clear_clicked()

        skipped = [c.args[0] for c in worker.skip_item.call_args_list]
        assert skipped == [item2, item3]  # PROCESSING item1 is preserved
        assert item1 not in skipped

    def test_remove_during_run_skips_item_in_worker(self, tab):
        """Removing a single row mid-run must also reach the worker (T-23)."""
        _add_ready_item(tab, "https://youtu.be/v1")
        item2 = _add_ready_item(tab, "https://youtu.be/v2")
        tab._on_mine_clicked()
        tab._on_item_started(0)
        worker = tab.worker_thread

        tab._on_remove_clicked(item2)

        worker.skip_item.assert_called_once_with(item2)

    def test_clear_resets_progress_widget_when_idle(self, tab):
        """Regression: clicking Clear after a stuck-bar scenario clears the bar."""
        # Simulate the post-bug screenshot state: queue idle, bar stuck on "Merging".
        _add_ready_item(tab, "https://youtu.be/v1")
        tab.progress_widget.set_indeterminate()
        tab.progress_widget.set_status("Merging")
        assert tab.worker_thread is None

        tab._on_clear_clicked()

        assert tab.progress_widget.progress_bar.maximum() == 100
        assert tab.progress_widget.progress_bar.value() == 0
        assert tab.progress_widget.status_label.text() == "Ready"

    def test_clear_during_run_does_not_reset_progress(self, tab):
        """Clearing READY items mid-run must not wipe the live progress display."""
        _add_ready_item(tab, "https://youtu.be/v1")
        _add_ready_item(tab, "https://youtu.be/v2")
        tab._on_mine_clicked()
        tab._on_item_started(0)  # item1 -> PROCESSING
        # Live progress emit for the in-flight item.
        tab._on_item_progress(0, "Downloading video", 42)
        assert "Downloading" in tab.progress_widget.status_label.text()

        tab._on_clear_clicked()

        # Bar still reflects the in-flight item — Clear did not reset it.
        assert "Downloading" in tab.progress_widget.status_label.text()
        assert tab.progress_widget.progress_bar.value() == 42


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

        probes = list(tab._add_flow._probe_workers)
        assert len(probes) == 2

        tab.shutdown()

        for p in probes:
            p.quit.assert_called()
            p.wait.assert_called()
        assert tab._add_flow._probe_workers == []

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

    def test_run_items_cleared_after_worker_finished(self, tab):
        """_run_items is reset to [] when the worker thread finishes."""
        _add_ready_item(tab, "https://youtu.be/v1")
        tab._on_mine_clicked()
        assert len(tab._run_items) == 1

        tab._on_worker_finished()
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
        # The add-flow controller adopts the same snapshot + fetcher, so
        # future probes classify against the updated limits.
        assert tab._add_flow._config is new_cfg
        assert tab._add_flow._fetcher is new_fetcher

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


# ---------------------------------------------------------------------------
# Playlist support (Issue #70)
# ---------------------------------------------------------------------------


def _resolve_playlist(tab, url: str, pl: PlaylistInfo) -> None:
    """Helper: simulate the resolve worker emitting ``playlist_resolved``."""
    tab._add_flow._on_playlist_resolved(url, classify_youtube_url(url), pl, tab._add_flow._playlist_generation)


class TestPlaylistAdd:
    """Playlist-shaped URLs spawn a resolve worker instead of a single probe."""

    def test_playlist_url_spawns_resolve_worker_not_single_probe(self, tab):
        tab.url_edit.setText(PLAYLIST_URL)
        tab._on_add_clicked()

        assert tab._playlist_resolve_worker_cls.call_count == 1
        assert tab._probe_worker_cls.call_count == 0
        # No row appears until the user confirms the expansion.
        assert tab._queue.all_items() == []
        assert tab.url_edit.text() == ""
        # Resolve worker constructed with the configured cap and started.
        kwargs = tab._playlist_resolve_worker_cls.call_args.kwargs
        assert kwargs["limit"] == tab._config.youtube_playlist_max
        tab._add_flow._playlist_resolve_worker.start.assert_called_once()

    def test_mixed_url_spawns_resolve_worker(self, tab):
        tab.url_edit.setText(MIXED_URL)
        tab._on_add_clicked()

        assert tab._playlist_resolve_worker_cls.call_count == 1
        assert tab._probe_worker_cls.call_count == 0

    def test_add_disabled_while_resolve_active(self, tab):
        tab.url_edit.setText(PLAYLIST_URL)
        tab._on_add_clicked()
        assert not tab.add_button.isEnabled()

        # finished → handle cleared → Add re-enabled.
        tab._add_flow._on_playlist_resolve_finished()
        assert tab.add_button.isEnabled()

    def test_plain_video_url_unaffected(self, tab):
        """Plain video URLs keep the existing single-probe path."""
        tab.url_edit.setText("https://www.youtube.com/watch?v=abcdefghijk")
        tab._on_add_clicked()

        assert tab._playlist_resolve_worker_cls.call_count == 0
        assert tab._probe_worker_cls.call_count == 1
        assert len(tab._queue.all_items()) == 1

    def test_second_playlist_add_while_probe_active_warns(self, tab):
        # First playlist resolves and expands; resolve worker then finishes,
        # leaving the playlist probe worker as the active guard.
        tab.url_edit.setText(PLAYLIST_URL)
        tab._on_add_clicked()
        _resolve_playlist(tab, PLAYLIST_URL, _make_playlist_info(n=2))
        tab._add_flow._on_playlist_resolve_finished()
        assert tab._add_flow._playlist_probe_worker is not None
        assert tab.add_button.isEnabled()

        tab.url_edit.setText(PLAYLIST_URL)
        tab._on_add_clicked()

        assert tab._playlist_resolve_worker_cls.call_count == 1  # no second resolve
        assert "already being added" in tab.log_widget.text_edit.toPlainText()

    def test_resolve_error_logged_and_recovers(self, tab):
        tab.url_edit.setText(PLAYLIST_URL)
        tab._on_add_clicked()

        tab._add_flow._on_playlist_resolve_error("yt-dlp exploded")
        tab._add_flow._on_playlist_resolve_finished()

        assert "yt-dlp exploded" in tab.log_widget.text_edit.toPlainText()
        assert tab._add_flow._playlist_resolve_worker is None
        assert tab.add_button.isEnabled()


class TestPlaylistResolved:
    """Resolved playlists expand (optionally via the choice dialog)."""

    def test_under_cap_pure_playlist_expands_without_dialog(self, tab):
        tab.url_edit.setText(PLAYLIST_URL)
        tab._on_add_clicked()
        pl = _make_playlist_info(n=3)

        with patch("anki_miner.gui.widgets.youtube_playlist_flow.QMessageBox") as mock_box:
            _resolve_playlist(tab, PLAYLIST_URL, pl)

        assert not mock_box.called
        assert not mock_box.method_calls

        items = tab._queue.all_items()
        assert len(items) == 3
        for item, entry in zip(items, pl.entries, strict=True):
            assert item.status == YouTubeItemStatus.PROBING
            assert item.video_id == entry.video_id
            assert item.display_title == entry.title
            assert item.url == entry.url
        # Sequential probe worker started with the entry URLs in order.
        args = tab._playlist_probe_worker_cls.call_args.args
        assert args[1] == [e.url for e in pl.entries]
        tab._add_flow._playlist_probe_worker.start.assert_called_once()

    def test_over_cap_truncates_and_passes_over_cap_flag(self, tab):
        # The add flow reads its own frozen snapshot — override it there.
        tab._add_flow._config = replace(tab._add_flow._config, youtube_playlist_max=3)
        tab.url_edit.setText(PLAYLIST_URL)
        tab._on_add_clicked()
        pl = _make_playlist_info(n=4)  # fetcher returns cap+1 untruncated

        with patch.object(tab._add_flow, "_ask_playlist_choice", return_value="playlist") as ask:
            _resolve_playlist(tab, PLAYLIST_URL, pl)

        ask.assert_called_once()
        _, _, cap, over_cap = ask.call_args.args
        assert cap == 3
        assert over_cap is True
        # Rows truncated to the cap, in playlist order.
        items = tab._queue.all_items()
        assert [i.video_id for i in items] == [e.video_id for e in pl.entries[:3]]

    def test_over_cap_by_total_count_only(self, tab):
        """total_count > cap flags over-cap even when fewer entries survived parsing."""
        # The add flow reads its own frozen snapshot — override it there.
        tab._add_flow._config = replace(tab._add_flow._config, youtube_playlist_max=3)
        tab.url_edit.setText(PLAYLIST_URL)
        tab._on_add_clicked()
        pl = _make_playlist_info(n=2, total_count=50)

        with patch.object(tab._add_flow, "_ask_playlist_choice", return_value="cancel") as ask:
            _resolve_playlist(tab, PLAYLIST_URL, pl)

        assert ask.call_args.args[3] is True  # over_cap

    def test_over_cap_cancel_creates_zero_rows(self, tab):
        # The add flow reads its own frozen snapshot — override it there.
        tab._add_flow._config = replace(tab._add_flow._config, youtube_playlist_max=3)
        tab.url_edit.setText(PLAYLIST_URL)
        tab._on_add_clicked()

        with patch.object(tab._add_flow, "_ask_playlist_choice", return_value="cancel"):
            _resolve_playlist(tab, PLAYLIST_URL, _make_playlist_info(n=4))

        assert tab._queue.all_items() == []
        assert tab._playlist_probe_worker_cls.call_count == 0

    def test_mixed_url_single_choice_uses_single_path(self, tab):
        tab.url_edit.setText(MIXED_URL)
        tab._on_add_clicked()

        with patch.object(tab._add_flow, "_ask_playlist_choice", return_value="single"):
            _resolve_playlist(tab, MIXED_URL, _make_playlist_info(n=3))

        items = tab._queue.all_items()
        assert len(items) == 1
        assert items[0].url == MIXED_URL
        assert tab._probe_worker_cls.call_count == 1
        assert tab._playlist_probe_worker_cls.call_count == 0

    def test_mixed_url_playlist_choice_expands(self, tab):
        tab.url_edit.setText(MIXED_URL)
        tab._on_add_clicked()

        with patch.object(tab._add_flow, "_ask_playlist_choice", return_value="playlist"):
            _resolve_playlist(tab, MIXED_URL, _make_playlist_info(n=3))

        assert len(tab._queue.all_items()) == 3
        assert tab._playlist_probe_worker_cls.call_count == 1

    def test_mixed_url_cancel_choice_does_nothing(self, tab):
        tab.url_edit.setText(MIXED_URL)
        tab._on_add_clicked()

        with patch.object(tab._add_flow, "_ask_playlist_choice", return_value="cancel"):
            _resolve_playlist(tab, MIXED_URL, _make_playlist_info(n=3))

        assert tab._queue.all_items() == []
        assert tab._probe_worker_cls.call_count == 0
        assert tab._playlist_probe_worker_cls.call_count == 0

    def test_late_resolve_after_clear_ignored(self, tab):
        tab.url_edit.setText(PLAYLIST_URL)
        tab._on_add_clicked()
        stale_generation = tab._add_flow._playlist_generation

        tab._on_clear_clicked()  # bumps the generation

        with patch.object(tab._add_flow, "_ask_playlist_choice") as ask:
            tab._add_flow._on_playlist_resolved(
                PLAYLIST_URL,
                classify_youtube_url(PLAYLIST_URL),
                _make_playlist_info(n=3),
                stale_generation,
            )

        ask.assert_not_called()
        assert tab._queue.all_items() == []


class TestPlaylistDedupe:
    """Expansion skips videos already queued and within-batch duplicates."""

    def test_dedupe_against_existing_queue(self, tab):
        pl = _make_playlist_info(n=3)
        # Existing READY item shares a video_id with the first playlist entry.
        _add_ready_item(tab, "https://youtu.be/preexisting", video_id=pl.entries[0].video_id)

        tab.url_edit.setText(PLAYLIST_URL)
        tab._on_add_clicked()
        _resolve_playlist(tab, PLAYLIST_URL, pl)

        new_items = tab._queue.all_items()[1:]
        assert [i.video_id for i in new_items] == [pl.entries[1].video_id, pl.entries[2].video_id]
        assert "Skipped 1 already-queued video(s)." in tab.log_widget.text_edit.toPlainText()

    def test_dedupe_within_batch(self, tab):
        entry = _make_playlist_entry(video_id="vid00000000", title="Dup")
        other = _make_playlist_entry(video_id="vid00000001", title="Other")
        pl = PlaylistInfo(playlist_id="PLabcdefghijkl", title="P", entries=(entry, entry, other), total_count=None)

        tab.url_edit.setText(PLAYLIST_URL)
        tab._on_add_clicked()
        _resolve_playlist(tab, PLAYLIST_URL, pl)

        assert [i.video_id for i in tab._queue.all_items()] == ["vid00000000", "vid00000001"]

    def test_bare_id_single_add_deduped_by_playlist_expansion(self, tab):
        """OVH-036: a still-PROBING bare-id single add must be recognised by the
        playlist dedup so the same video isn't fetched twice.

        Before the fix, ``item.url`` was the raw bare id (e.g. ``"dQw4w9WgXcQ"``);
        ``classify_youtube_url("dQw4w9WgXcQ")`` → host not YouTube → video_id None,
        so ``existing_ids.discard(None)`` silently dropped it and a playlist
        containing that id added a duplicate row.  After the fix, bare ids are
        normalised to ``https://www.youtube.com/watch?v=<id>`` at enqueue time so
        the item URL always classifies and the dedup fires.
        """
        # Add as a bare id — it must land in PROBING state (no probe done yet)
        # so video_id is None on the item (normal probe-not-complete state).
        bare_id = "dQw4w9WgXcQ"
        tab.url_edit.setText(bare_id)
        tab._on_add_clicked()
        bare_item = tab._queue.all_items()[-1]
        # Still probing: video_id not yet populated by a probe result.
        assert bare_item.video_id is None
        assert bare_item.status == YouTubeItemStatus.PROBING

        # Expand a playlist that contains the same video id.
        pl = PlaylistInfo(
            playlist_id="PLtest",
            title="P",
            entries=(_make_playlist_entry(video_id=bare_id),),
            total_count=None,
        )
        tab.url_edit.setText(PLAYLIST_URL)
        tab._on_add_clicked()
        _resolve_playlist(tab, PLAYLIST_URL, pl)

        # The bare-id item should survive; the playlist duplicate must be skipped.
        items = tab._queue.all_items()
        assert len(items) == 1, "Duplicate playlist entry must have been deduped"
        assert "Skipped 1 already-queued video(s)." in tab.log_widget.text_edit.toPlainText()

    def test_all_duplicates_skips_probe_worker(self, tab):
        pl = _make_playlist_info(n=2)
        for entry in pl.entries:
            _add_ready_item(tab, f"https://youtu.be/{entry.video_id}", video_id=entry.video_id)

        tab.url_edit.setText(PLAYLIST_URL)
        tab._on_add_clicked()
        _resolve_playlist(tab, PLAYLIST_URL, pl)

        assert len(tab._queue.all_items()) == 2  # only the pre-existing items
        assert tab._playlist_probe_worker_cls.call_count == 0
        assert tab._add_flow._playlist_probe_worker is None


class TestPlaylistEntryProbes:
    """Per-entry probe signals reuse the single-video classification path."""

    def _expand(self, tab, n: int = 3):
        tab.url_edit.setText(PLAYLIST_URL)
        tab._on_add_clicked()
        pl = _make_playlist_info(n=n)
        _resolve_playlist(tab, PLAYLIST_URL, pl)
        tab._add_flow._on_playlist_resolve_finished()
        return tab._queue.all_items()

    def test_entry_probed_marks_ready_via_classification(self, tab):
        items = self._expand(tab)

        info = _make_video_info(video_id=items[0].video_id or "vid00000000")
        tab._add_flow._on_playlist_entry_probed(0, info)

        assert items[0].status == YouTubeItemStatus.READY
        assert items[0].video_info is info
        assert items[0].resolved_sub_mode == "manual_only"
        assert tab.mine_button.isEnabled()

    def test_entry_probed_no_ja_subs_marks_probe_error(self, tab):
        items = self._expand(tab)

        info = _make_video_info(has_manual_ja_subs=False, has_auto_ja_subs=False)
        tab._add_flow._on_playlist_entry_probed(1, info)

        assert items[1].status == YouTubeItemStatus.PROBE_ERROR
        assert "subtitles" in (items[1].error_message or "").lower()

    def test_entry_failed_marks_probe_error_with_message(self, tab):
        items = self._expand(tab)

        tab._add_flow._on_playlist_entry_failed(2, "yt-dlp exploded")

        assert items[2].status == YouTubeItemStatus.PROBE_ERROR
        assert items[2].error_message == "yt-dlp exploded"

    def test_removed_row_mid_probe_skipped(self, tab):
        items = self._expand(tab)
        removed = items[0]
        tab._on_remove_clicked(removed)

        # Late signal for the removed row: no crash, no status mutation.
        tab._add_flow._on_playlist_entry_probed(0, _make_video_info())
        assert removed.status == YouTubeItemStatus.PROBING
        assert removed.video_info is None

        # Mapping unaffected for surviving rows.
        tab._add_flow._on_playlist_entry_probed(1, _make_video_info())
        assert items[1].status == YouTubeItemStatus.READY

    def test_probe_finished_clears_state(self, tab):
        self._expand(tab)
        assert tab._add_flow._playlist_probe_worker is not None
        assert tab._add_flow._playlist_probe_items != []

        tab._add_flow._on_playlist_probe_finished()

        assert tab._add_flow._playlist_probe_worker is None
        assert tab._add_flow._playlist_probe_items == []


class TestPlaylistClearAndShutdown:
    """Clear cancels the playlist probe; shutdown waits on both workers."""

    def test_clear_cancels_playlist_probe_worker(self, tab):
        tab.url_edit.setText(PLAYLIST_URL)
        tab._on_add_clicked()
        _resolve_playlist(tab, PLAYLIST_URL, _make_playlist_info(n=2))
        worker = tab._add_flow._playlist_probe_worker
        assert worker is not None

        tab._on_clear_clicked()

        worker.cancel.assert_called_once()
        worker.wait.assert_not_called()  # never block the GUI thread
        assert tab._queue.all_items() == []

    def test_shutdown_cancels_and_waits_playlist_workers(self, tab):
        tab.url_edit.setText(PLAYLIST_URL)
        tab._on_add_clicked()
        resolve_worker = tab._add_flow._playlist_resolve_worker
        # Start the probe worker directly so both are simultaneously active.
        tab._add_flow._expand_playlist(list(_make_playlist_info(n=2).entries), "P")
        probe_worker = tab._add_flow._playlist_probe_worker
        assert resolve_worker is not None and probe_worker is not None

        tab.shutdown()

        probe_worker.cancel.assert_called_once()
        probe_worker.quit.assert_called_once()
        probe_worker.wait.assert_called_once()
        resolve_worker.quit.assert_called_once()
        resolve_worker.wait.assert_called_once()
        assert tab._add_flow._playlist_probe_worker is None
        assert tab._add_flow._playlist_resolve_worker is None
        assert tab._add_flow._playlist_probe_items == []


class TestAskPlaylistChoice:
    """Dialog wording/buttons for the three resolved-playlist shapes."""

    def test_pure_playlist_under_cap_returns_playlist_without_messagebox(self, tab):
        pl = _make_playlist_info(n=3)
        with patch("anki_miner.gui.widgets.youtube_playlist_flow.QMessageBox") as mock_box:
            choice = tab._add_flow._ask_playlist_choice(classify_youtube_url(PLAYLIST_URL), pl, 100, False)
        assert choice == "playlist"
        assert not mock_box.called

    def test_pure_playlist_over_cap_offers_add_first_cap(self, tab):
        pl = _make_playlist_info(n=4, total_count=50)
        with patch("anki_miner.gui.widgets.youtube_playlist_flow.QMessageBox") as mock_box:
            instance = mock_box.return_value
            playlist_button = MagicMock(name="PlaylistButton")
            instance.addButton.return_value = playlist_button
            instance.clickedButton.return_value = playlist_button

            choice = tab._add_flow._ask_playlist_choice(classify_youtube_url(PLAYLIST_URL), pl, 3, True)

        assert choice == "playlist"
        labels = [c.args[0] for c in instance.addButton.call_args_list if isinstance(c.args[0], str)]
        assert labels == ["Add first 3"]
        assert "has 50 videos" in instance.setText.call_args.args[0]

    def test_mixed_under_cap_offers_add_all(self, tab):
        pl = _make_playlist_info(n=3)
        with patch("anki_miner.gui.widgets.youtube_playlist_flow.QMessageBox") as mock_box:
            instance = mock_box.return_value
            buttons = [MagicMock(name="SingleButton"), MagicMock(name="PlaylistButton")]
            instance.addButton.side_effect = buttons + [MagicMock(name="CancelButton")]
            instance.clickedButton.return_value = buttons[0]

            choice = tab._add_flow._ask_playlist_choice(classify_youtube_url(MIXED_URL), pl, 100, False)

        assert choice == "single"
        labels = [c.args[0] for c in instance.addButton.call_args_list if isinstance(c.args[0], str)]
        assert labels == ["Just this video", "Add all 3"]
        assert "is part of the playlist 'My Playlist' (3 videos)" in instance.setText.call_args.args[0]

    def test_mixed_over_cap_unknown_total_uses_more_than_cap(self, tab):
        pl = _make_playlist_info(n=4, total_count=None)
        with patch("anki_miner.gui.widgets.youtube_playlist_flow.QMessageBox") as mock_box:
            instance = mock_box.return_value
            instance.clickedButton.return_value = MagicMock(name="Unmatched")

            choice = tab._add_flow._ask_playlist_choice(classify_youtube_url(MIXED_URL), pl, 3, True)

        assert choice == "cancel"
        labels = [c.args[0] for c in instance.addButton.call_args_list if isinstance(c.args[0], str)]
        assert labels == ["Just this video", "Add first 3 of more than 3"]
