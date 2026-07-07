"""Tests for :class:`YouTubeQueueWorker`.

The queue worker drives a list of :class:`YouTubeQueueItem` through fetch +
mine sequentially with retry-once on :class:`YouTubeFetchError`. Tests
exercise the worker body synchronously by calling ``run()`` directly; Qt
threading itself is not under test.
"""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from anki_miner.exceptions.youtube import YouTubeFetchError
from anki_miner.gui.workers.youtube_queue_worker import (
    YouTubeQueueWorker,
    _QueueMiningProgressAdapter,
)
from anki_miner.models.youtube import VideoInfo
from anki_miner.models.youtube_queue import YouTubeItemStatus, YouTubeQueueItem


def _make_video_info(video_id: str = "abc", title: str = "Some Title") -> VideoInfo:
    """Build a minimal VideoInfo with the given id/title."""
    return VideoInfo(
        video_id=video_id,
        title=title,
        duration_s=120,
        has_manual_ja_subs=True,
        has_auto_ja_subs=False,
        is_live=False,
        is_age_restricted=False,
    )


class _SignalCapture:
    """Collect emissions from a Qt signal for later inspection."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __call__(self, *args) -> None:
        self.calls.append(args)


@pytest.fixture
def youtube_config(test_config, tmp_path):
    """Config pointing media_temp_folder into a test-owned tmp_path."""
    return replace(test_config, media_temp_folder=tmp_path / "temp_media")


@pytest.fixture
def mock_processor():
    """MagicMock stand-in for EpisodeProcessor."""
    processor = MagicMock()
    processor.process_youtube_url = MagicMock(return_value=MagicMock(name="ProcessingResult"))
    return processor


def _make_item(
    url: str = "https://www.youtube.com/watch?v=abc",
    video_id: str = "abc",
    sub_mode: str = "manual_only",
    title: str = "Some Title",
) -> YouTubeQueueItem:
    """Build a READY queue item with the given identity."""
    return YouTubeQueueItem(
        url=url,
        status=YouTubeItemStatus.READY,
        video_id=video_id,
        resolved_sub_mode=sub_mode,  # type: ignore[arg-type]
        video_info=_make_video_info(video_id=video_id, title=title),
    )


@pytest.fixture
def make_worker(qapp, mock_processor, youtube_config):
    """Factory producing a YouTubeQueueWorker with sensible defaults."""

    def _make(
        items: list[YouTubeQueueItem] | None = None,
        curation_callback=None,
        preview_mode: bool = False,
    ) -> YouTubeQueueWorker:
        if items is None:
            items = [_make_item()]
        return YouTubeQueueWorker(
            processor=mock_processor,
            config=youtube_config,
            items=items,
            curation_callback=curation_callback,
            preview_mode=preview_mode,
        )

    return _make


def _connect_all(worker: YouTubeQueueWorker):
    """Wire capture objects to all queue worker signals; return them as a dict."""
    captures = {
        "started": _SignalCapture(),
        "progress": _SignalCapture(),
        "finished": _SignalCapture(),
        "queue_finished": _SignalCapture(),
    }
    worker.item_started.connect(captures["started"])
    worker.item_progress.connect(captures["progress"])
    worker.item_finished.connect(captures["finished"])
    worker.queue_finished.connect(captures["queue_finished"])
    return captures


# ---------------------------------------------------------------------------
# All success
# ---------------------------------------------------------------------------


def test_all_success_emits_per_item_finished_and_queue_finished(make_worker, mock_processor):
    items = [
        _make_item(url="https://www.youtube.com/watch?v=a", video_id="a"),
        _make_item(url="https://www.youtube.com/watch?v=b", video_id="b"),
        _make_item(url="https://www.youtube.com/watch?v=c", video_id="c"),
    ]
    results = ["R_A", "R_B", "R_C"]
    mock_processor.process_youtube_url.side_effect = lambda **kw: results.pop(0)

    worker = make_worker(items=items)
    caps = _connect_all(worker)
    worker.run()

    # item_started fired once per item in order
    assert caps["started"].calls == [(0,), (1,), (2,)]

    # item_finished: each success with attempts=1
    assert caps["finished"].calls == [
        (0, "R_A", None, 1),
        (1, "R_B", None, 1),
        (2, "R_C", None, 1),
    ]

    # queue_finished fires exactly once at the end
    assert len(caps["queue_finished"].calls) == 1

    assert mock_processor.process_youtube_url.call_count == 3


# ---------------------------------------------------------------------------
# Retry-once succeeds
# ---------------------------------------------------------------------------


def test_retry_once_succeeds_emits_finished_with_attempts_two(make_worker, mock_processor):
    items = [_make_item(video_id="a"), _make_item(video_id="b"), _make_item(video_id="c")]

    call_counter = {"b": 0}

    def _side_effect(**kw):
        if kw["video_id"] == "b":
            call_counter["b"] += 1
            if call_counter["b"] == 1:
                raise YouTubeFetchError("transient")
            return "R_B"
        return f"R_{kw['video_id'].upper()}"

    mock_processor.process_youtube_url.side_effect = _side_effect

    worker = make_worker(items=items)
    caps = _connect_all(worker)
    worker.run()

    assert caps["finished"].calls == [
        (0, "R_A", None, 1),
        (1, "R_B", None, 2),
        (2, "R_C", None, 1),
    ]
    assert len(caps["queue_finished"].calls) == 1


# ---------------------------------------------------------------------------
# Retry-twice fails
# ---------------------------------------------------------------------------


def test_retry_twice_fails_emits_error_and_queue_continues(make_worker, mock_processor):
    items = [_make_item(video_id="a"), _make_item(video_id="b"), _make_item(video_id="c")]

    def _side_effect(**kw):
        if kw["video_id"] == "b":
            raise YouTubeFetchError("persistent")
        return f"R_{kw['video_id'].upper()}"

    mock_processor.process_youtube_url.side_effect = _side_effect

    worker = make_worker(items=items)
    caps = _connect_all(worker)
    worker.run()

    assert caps["finished"].calls == [
        (0, "R_A", None, 1),
        (1, None, "YouTubeFetchError: persistent", 2),
        (2, "R_C", None, 1),
    ]
    assert len(caps["queue_finished"].calls) == 1


# ---------------------------------------------------------------------------
# Non-fetch exception aborts that item
# ---------------------------------------------------------------------------


def test_non_fetch_exception_no_retry_continues_queue(make_worker, mock_processor):
    items = [_make_item(video_id="a"), _make_item(video_id="b")]

    workspaces: list = []

    def _side_effect(**kw):
        if kw["video_id"] == "a":
            workspaces.append(kw["workspace"])
            raise ValueError("boom")
        return "R_B"

    mock_processor.process_youtube_url.side_effect = _side_effect

    worker = make_worker(items=items)
    caps = _connect_all(worker)
    worker.run()

    # idx=0 fails after exactly one attempt, idx=1 still runs
    assert caps["finished"].calls == [
        (0, None, "ValueError: boom", 1),
        (1, "R_B", None, 1),
    ]
    assert len(caps["queue_finished"].calls) == 1
    # Workspace for the failed item must be cleaned up.
    for ws in workspaces:
        assert not ws.exists()


# ---------------------------------------------------------------------------
# Cancel during item
# ---------------------------------------------------------------------------


def test_cancel_during_item_returns_without_emitting_finished(make_worker, mock_processor):
    items = [_make_item(video_id="a"), _make_item(video_id="b")]

    workspaces: list = []

    def _cancel_then_raise(**kw):
        # Simulate the fetcher's psutil kill path: cancel_event gets set,
        # then YouTubeFetchError is raised.
        workspaces.append(kw["workspace"])
        kw["cancel_event"].set()
        raise YouTubeFetchError("Cancelled")

    mock_processor.process_youtube_url.side_effect = _cancel_then_raise

    worker = make_worker(items=items)
    caps = _connect_all(worker)
    worker.run()

    # idx=0 was started but no finished emit (cancel during retry path returns)
    assert caps["started"].calls == [(0,)]
    assert caps["finished"].calls == []
    # Should not retry the cancelled fetch
    assert mock_processor.process_youtube_url.call_count == 1
    # queue_finished does NOT fire when return-early triggers (worker returns
    # from inside the except clause). Per spec snippet, ``return`` skips the
    # queue_finished.emit at the bottom of run().
    assert caps["queue_finished"].calls == []
    # Workspace must be cleaned up even on cancel.
    for ws in workspaces:
        assert not ws.exists()


# ---------------------------------------------------------------------------
# Cancel mid-mine (T-01): processor returns a cancelled result, no exception
# ---------------------------------------------------------------------------


def test_cancel_mid_item_skips_remaining_items_queue_finished_fires(make_worker, mock_processor):
    """Stop All during item 1's mining run stops the queue before item 2.

    With cancel bridged into the pipeline (T-01), process_youtube_url returns a
    cancelled ProcessingResult instead of mining to completion; the worker's
    loop-top check must then skip every remaining item while still emitting
    queue_finished (cancel-between-items contract, unlike the mid-fetch
    exception path which returns early).
    """
    items = [_make_item(video_id="a"), _make_item(video_id="b"), _make_item(video_id="c")]

    def _cancel_mid_mine(**kw):
        kw["cancel_event"].set()  # user pressed Stop All mid-pipeline
        return "R_CANCELLED"  # processor returns a cancelled result, no raise

    mock_processor.process_youtube_url.side_effect = _cancel_mid_mine

    worker = make_worker(items=items)
    caps = _connect_all(worker)
    worker.run()

    # Items 2 and 3 never started.
    assert mock_processor.process_youtube_url.call_count == 1
    assert caps["started"].calls == [(0,)]
    assert caps["finished"].calls == [(0, "R_CANCELLED", None, 1)]
    # queue_finished still fires: the loop-top break exits the loop normally.
    assert len(caps["queue_finished"].calls) == 1


# ---------------------------------------------------------------------------
# Skip channel (T-23): GUI-removed items must not be mined
# ---------------------------------------------------------------------------


def test_skip_item_drops_queued_items_mid_run(make_worker, mock_processor):
    """Clear during a run must stop the worker from mining removed items.

    The worker iterates its constructor snapshot; items the GUI dropped used
    to be fetched and mined anyway — cards appeared for rows that no longer
    existed and the end-of-run summary undercounted.
    """
    items = [_make_item(video_id="a"), _make_item(video_id="b"), _make_item(video_id="c")]
    worker_box: dict = {}

    def _clear_rest_while_mining_first(**kw):
        # Simulate the user clicking Clear while item 1 is PROCESSING: the
        # GUI drops the non-PROCESSING tail into the worker's skip channel.
        worker_box["worker"].skip_item(items[1])
        worker_box["worker"].skip_item(items[2])
        return "R_A"

    mock_processor.process_youtube_url.side_effect = _clear_rest_while_mining_first

    worker = make_worker(items=items)
    worker_box["worker"] = worker
    caps = _connect_all(worker)
    worker.run()

    # Only item 1 was mined.
    assert mock_processor.process_youtube_url.call_count == 1
    assert mock_processor.process_youtube_url.call_args.kwargs["video_id"] == "a"
    # No signals for the dropped items.
    assert caps["started"].calls == [(0,)]
    assert caps["finished"].calls == [(0, "R_A", None, 1)]
    # The queue still completes normally.
    assert len(caps["queue_finished"].calls) == 1


def test_skip_item_before_run_skips_only_that_item(make_worker, mock_processor):
    """A skip recorded before run() starts drops exactly that item."""
    items = [_make_item(video_id="a"), _make_item(video_id="b"), _make_item(video_id="c")]
    mock_processor.process_youtube_url.side_effect = lambda **kw: f"R_{kw['video_id']}"

    worker = make_worker(items=items)
    worker.skip_item(items[1])
    caps = _connect_all(worker)
    worker.run()

    mined = [c.kwargs["video_id"] for c in mock_processor.process_youtube_url.call_args_list]
    assert mined == ["a", "c"]
    # idx values still match the frozen snapshot positions (0 and 2).
    assert caps["started"].calls == [(0,), (2,)]
    assert caps["finished"].calls == [(0, "R_a", None, 1), (2, "R_c", None, 1)]
    assert len(caps["queue_finished"].calls) == 1


# ---------------------------------------------------------------------------
# Cancel before first item
# ---------------------------------------------------------------------------


def test_cancel_before_run_emits_queue_finished_only(make_worker, mock_processor):
    items = [_make_item(video_id="a"), _make_item(video_id="b")]
    worker = make_worker(items=items)
    worker.cancel()
    caps = _connect_all(worker)

    worker.run()

    assert caps["started"].calls == []
    assert caps["finished"].calls == []
    assert mock_processor.process_youtube_url.call_count == 0
    # Spec: outer ``if self.is_cancelled: break`` exits the for loop, but
    # queue_finished.emit() lives OUTSIDE the loop, so it fires.
    assert len(caps["queue_finished"].calls) == 1


# ---------------------------------------------------------------------------
# Workspace lifecycle per attempt
# ---------------------------------------------------------------------------


def test_each_attempt_gets_unique_workspace_and_is_cleaned(make_worker, mock_processor, youtube_config):
    items = [_make_item(video_id="a")]

    workspaces: list = []
    call_counter = {"n": 0}

    def _side_effect(**kw):
        ws = kw["workspace"]
        assert ws.is_dir()
        workspaces.append(ws)
        call_counter["n"] += 1
        if call_counter["n"] == 1:
            raise YouTubeFetchError("transient")
        return "R_A"

    mock_processor.process_youtube_url.side_effect = _side_effect

    worker = make_worker(items=items)
    caps = _connect_all(worker)
    worker.run()

    # Two attempts → two distinct workspaces
    assert len(workspaces) == 2
    assert workspaces[0] != workspaces[1]
    # Both must live under <media_temp>/youtube/
    for ws in workspaces:
        assert ws.parent == youtube_config.media_temp_folder / "youtube"
        assert ws.name.startswith("run-")
    # Both cleaned up
    for ws in workspaces:
        assert not ws.exists()

    assert caps["finished"].calls == [(0, "R_A", None, 2)]


# ---------------------------------------------------------------------------
# BotDetectionError (YouTubeFetchError subclass) retried + workspace cleaned
# ---------------------------------------------------------------------------


def test_bot_detection_error_workspace_cleaned(qapp, mock_processor, youtube_config):
    """BotDetectionError (subclass of YouTubeFetchError) follows the retry+cleanup path."""
    from anki_miner.exceptions.youtube import BotDetectionError

    item = _make_item("https://www.youtube.com/watch?v=bot", "bot")
    workspaces: list = []

    def _record_then_raise(**kwargs):
        workspaces.append(kwargs["workspace"])
        raise BotDetectionError("sign in to confirm")

    mock_processor.process_youtube_url.side_effect = _record_then_raise

    worker = YouTubeQueueWorker(
        processor=mock_processor,
        config=youtube_config,
        items=[item],
        curation_callback=None,
        preview_mode=False,
    )
    worker.run()

    # Two attempts (retry-once), each its own workspace, both cleaned.
    assert len(workspaces) == 2
    for ws in workspaces:
        assert not ws.exists()


# ---------------------------------------------------------------------------
# curation_callback forwarded unconditionally (dead-code-bug fix)
# ---------------------------------------------------------------------------


def test_non_preview_forwards_curation_callback(make_worker, mock_processor):
    """Non-preview runs must forward curation_callback (not None).

    process_episode internally gates invocation on not preview_mode, so
    passing it unconditionally is correct and was the dead-code bug.
    """

    def _curation(words):
        return words

    items = [_make_item(video_id="a")]
    worker = make_worker(items=items, curation_callback=_curation, preview_mode=False)
    worker.run()

    kwargs = mock_processor.process_youtube_url.call_args.kwargs
    assert kwargs["curation_callback"] is _curation
    assert kwargs["preview_mode"] is False


def test_preview_mode_true_forwards_curation_callback(make_worker, mock_processor):
    def _curation(words):
        return words

    items = [_make_item(video_id="a")]
    worker = make_worker(items=items, curation_callback=_curation, preview_mode=True)
    worker.run()

    kwargs = mock_processor.process_youtube_url.call_args.kwargs
    assert kwargs["curation_callback"] is _curation
    assert kwargs["preview_mode"] is True


def test_none_curation_callback_passed_through(make_worker, mock_processor):
    """When curation_callback is None it is forwarded as None (disabled)."""
    items = [_make_item(video_id="a")]
    worker = make_worker(items=items, curation_callback=None, preview_mode=False)
    worker.run()

    kwargs = mock_processor.process_youtube_url.call_args.kwargs
    assert kwargs["curation_callback"] is None


# ---------------------------------------------------------------------------
# source_label forwarded from video_info.title (Issue #69)
# ---------------------------------------------------------------------------


def test_source_label_forwarded_from_video_info_title(make_worker, mock_processor):
    """The worker passes the item's video title as source_label."""
    items = [_make_item(video_id="a", title="My Great Video")]
    worker = make_worker(items=items)
    worker.run()

    kwargs = mock_processor.process_youtube_url.call_args.kwargs
    assert kwargs["source_label"] == "My Great Video"


def test_ready_guard_raises_when_video_info_missing(make_worker, mock_processor):
    """A READY item lacking video_info is a terminal error (probe incomplete)."""
    item = _make_item(video_id="a")
    item = replace(item, video_info=None)
    worker = make_worker(items=[item])
    caps = _connect_all(worker)
    worker.run()

    # process_youtube_url is never reached; the item ends with an error.
    mock_processor.process_youtube_url.assert_not_called()
    assert len(caps["finished"].calls) == 1
    idx, result, error, _attempts = caps["finished"].calls[0]
    assert result is None
    assert "video_info" in error


# ---------------------------------------------------------------------------
# item_started ordering
# ---------------------------------------------------------------------------


def test_item_started_fires_in_queue_order(make_worker, mock_processor):
    items = [_make_item(video_id=f"v{i}") for i in range(5)]
    mock_processor.process_youtube_url.side_effect = lambda **kw: f"R_{kw['video_id']}"

    worker = make_worker(items=items)
    caps = _connect_all(worker)
    worker.run()

    assert [c[0] for c in caps["started"].calls] == [0, 1, 2, 3, 4]


# ---------------------------------------------------------------------------
# cancel_event identity forwarded per call
# ---------------------------------------------------------------------------


def test_cancel_event_identity_forwarded_each_call(make_worker, mock_processor):
    items = [_make_item(video_id="a"), _make_item(video_id="b")]
    mock_processor.process_youtube_url.side_effect = lambda **kw: "R"

    worker = make_worker(items=items)
    worker.run()

    for call in mock_processor.process_youtube_url.call_args_list:
        assert call.kwargs["cancel_event"] is worker._cancel_event


# ---------------------------------------------------------------------------
# Item attributes flow into process_youtube_url
# ---------------------------------------------------------------------------


def test_item_attributes_passed_to_processor(make_worker, mock_processor):
    items = [
        _make_item(url="https://youtu.be/x1", video_id="x1", sub_mode="manual_only"),
        _make_item(url="https://youtu.be/x2", video_id="x2", sub_mode="auto_only"),
    ]
    mock_processor.process_youtube_url.side_effect = lambda **kw: "R"

    worker = make_worker(items=items)
    worker.run()

    calls = mock_processor.process_youtube_url.call_args_list
    assert calls[0].kwargs["url"] == "https://youtu.be/x1"
    assert calls[0].kwargs["video_id"] == "x1"
    assert calls[0].kwargs["sub_mode"] == "manual_only"
    assert calls[1].kwargs["url"] == "https://youtu.be/x2"
    assert calls[1].kwargs["video_id"] == "x2"
    assert calls[1].kwargs["sub_mode"] == "auto_only"


# ---------------------------------------------------------------------------
# Progress adapter
# ---------------------------------------------------------------------------


def test_queue_mining_progress_adapter_bakes_idx_into_emit():
    emitted: list[tuple[int, str, int]] = []
    adapter = _QueueMiningProgressAdapter(
        idx=7,
        emit=lambda idx, label, pct: emitted.append((idx, label, pct)),
    )

    adapter.on_start(10, "Extracting media")
    adapter.on_progress(5, "word-05")
    adapter.on_complete()

    assert emitted == [
        (7, "Extracting media", 0),
        (7, "word-05", 50),
        (7, "Extracting media", 100),
    ]


def test_queue_mining_progress_adapter_on_error_emits_nothing():
    emitted: list = []
    adapter = _QueueMiningProgressAdapter(
        idx=0,
        emit=lambda *args: emitted.append(args),
    )
    adapter.on_error("word", "boom")
    assert emitted == []


def test_mining_progress_adapter_handles_zero_total():
    """on_start(0, ...) clamps total to 1 so on_progress(1, ...) yields 100%."""
    emitted: list[tuple[int, str, int]] = []
    adapter = _QueueMiningProgressAdapter(
        idx=3,
        emit=lambda idx, label, pct: emitted.append((idx, label, pct)),
    )
    adapter.on_start(0, "Edge case")
    adapter.on_progress(1, "item")

    # on_start emits pct=0; on_progress emits 1/1 = 100% under the clamp.
    # The label is the self-prefixed item string as-is (no stage-desc glue).
    assert emitted[-1] == (3, "item", 100)


# ---------------------------------------------------------------------------
# Curation media context attrs set from constructor
# ---------------------------------------------------------------------------


def test_curation_processor_and_offset_set_from_constructor(qapp, mock_processor, youtube_config):
    """curation_processor and _curation_offset are initialised from constructor args."""
    worker = YouTubeQueueWorker(
        processor=mock_processor,
        config=youtube_config,
        items=[],
        curation_callback=None,
        preview_mode=False,
    )
    assert worker.curation_processor is mock_processor
    assert worker._curation_offset == youtube_config.subtitle_offset
    assert worker._curation_video is None
    assert worker._curation_subtitle is None


# ---------------------------------------------------------------------------
# on_fetched forwarded and _capture_curation_media populates paths
# ---------------------------------------------------------------------------


def test_on_fetched_kwarg_passed_to_process_youtube_url(make_worker, mock_processor):
    """_mine_one must pass on_fetched= to process_youtube_url (the _capture_curation_media method)."""
    items = [_make_item(video_id="a")]
    worker = make_worker(items=items)
    worker.run()

    kwargs = mock_processor.process_youtube_url.call_args.kwargs
    assert "on_fetched" in kwargs
    # Bound methods compare by __func__ + __self__; use that to avoid
    # creating two distinct bound method objects via two attribute accesses.
    on_fetched = kwargs["on_fetched"]
    assert callable(on_fetched)
    assert on_fetched.__func__ is YouTubeQueueWorker._capture_curation_media
    assert on_fetched.__self__ is worker


def test_capture_curation_media_populates_video_and_subtitle(qapp, mock_processor, youtube_config, tmp_path):
    """Invoking _capture_curation_media sets _curation_video and _curation_subtitle."""
    from anki_miner.models.youtube import FetchedMedia

    worker = YouTubeQueueWorker(
        processor=mock_processor,
        config=youtube_config,
        items=[],
        curation_callback=None,
        preview_mode=False,
    )

    video_path = tmp_path / "video.mp4"
    sub_path = tmp_path / "subs.vtt"
    fetched = FetchedMedia(video_file=video_path, subtitle_file=sub_path, sub_source="manual")

    assert worker._curation_video is None
    assert worker._curation_subtitle is None

    worker._capture_curation_media(fetched)

    assert worker._curation_video == video_path
    assert worker._curation_subtitle == sub_path


def test_on_fetched_invoked_populates_curation_paths(qapp, mock_processor, youtube_config, tmp_path):
    """End-to-end: when process_youtube_url calls on_fetched, _curation_* attrs are set."""
    from anki_miner.models.youtube import FetchedMedia

    video_path = tmp_path / "ep01.mp4"
    sub_path = tmp_path / "ep01.vtt"
    fetched = FetchedMedia(video_file=video_path, subtitle_file=sub_path, sub_source="auto")

    def _call_on_fetched(**kw):
        # Simulate process_youtube_url calling the on_fetched hook mid-run.
        kw["on_fetched"](fetched)
        return MagicMock(name="ProcessingResult")

    mock_processor.process_youtube_url.side_effect = _call_on_fetched

    item = _make_item(video_id="a")
    worker = YouTubeQueueWorker(
        processor=mock_processor,
        config=youtube_config,
        items=[item],
        curation_callback=None,
        preview_mode=False,
    )
    worker.run()

    assert worker._curation_video == video_path
    assert worker._curation_subtitle == sub_path


# ---------------------------------------------------------------------------
# fetch_progress_emit
# ---------------------------------------------------------------------------


def test_fetch_progress_emit_clamps_and_handles_none(make_worker, mock_processor):
    items = [_make_item(video_id="a")]
    captured_fetch_cb = []

    def _capture(**kw):
        captured_fetch_cb.append(kw["fetch_progress_cb"])
        return "R"

    mock_processor.process_youtube_url.side_effect = _capture

    worker = make_worker(items=items)
    caps = _connect_all(worker)
    worker.run()

    fetch_cb = captured_fetch_cb[0]
    # Invoke the captured fetch progress callback synthetically.
    fetch_cb("Downloading", 0.5)
    fetch_cb("Merging", None)
    fetch_cb("Downloading", 1.5)
    fetch_cb("Downloading", -0.5)

    assert caps["progress"].calls == [
        (0, "Downloading", 50),
        (0, "Merging", -1),
        (0, "Downloading", 100),
        (0, "Downloading", 0),
    ]


# ---------------------------------------------------------------------------
# Workspace mkdir failure is a per-item error, not a queue killer (T-29)
# ---------------------------------------------------------------------------


def test_workspace_alloc_failure_is_per_item_not_queue_killer(make_worker, mock_processor, tmp_path):
    """An mkdir OSError during workspace allocation must end *that* item with
    an error and let the queue continue — not propagate out of ``run()`` and
    strand the queue (no ``item_finished`` / ``queue_finished``).

    A non-``YouTubeFetchError`` ends the item without a retry, so item 0
    allocates exactly once (it raises) and item 1 allocates exactly once
    (it succeeds): a plain call counter on the allocation hook is enough.
    """
    items = [
        _make_item(url="https://www.youtube.com/watch?v=a", video_id="a"),
        _make_item(url="https://www.youtube.com/watch?v=b", video_id="b"),
    ]
    mock_processor.process_youtube_url.return_value = "R_B"

    worker = make_worker(items=items)
    caps = _connect_all(worker)

    calls = {"n": 0}

    def _alloc():
        calls["n"] += 1
        if calls["n"] == 1:  # item 0's allocation: simulate ENOSPC / perms
            raise OSError(28, "No space left on device")
        ws = tmp_path / f"ws-{calls['n']}"
        ws.mkdir()
        return ws

    worker._allocate_workspace = _alloc  # type: ignore[method-assign,assignment]

    worker.run()

    # Both items reported; item 0 errored, item 1 succeeded; queue finished.
    assert len(caps["finished"].calls) == 2
    idx0, res0, err0, _ = caps["finished"].calls[0]
    idx1, res1, err1, _ = caps["finished"].calls[1]
    assert idx0 == 0 and res0 is None and "OSError" in err0
    assert idx1 == 1 and res1 == "R_B" and err1 is None
    assert len(caps["queue_finished"].calls) == 1


# ---------------------------------------------------------------------------
# OVH-062 — workspace permissions
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits not applicable on Windows")
def test_allocate_workspace_mode_0o700(make_worker):
    """OVH-062: the allocated workspace must have mode 0o700 (owner-only).

    Cookie-authenticated video/subtitle downloads land in this directory;
    world-readable permissions would expose them to other local users.
    """
    worker = make_worker()
    workspace = worker._allocate_workspace()
    try:
        mode = os.stat(workspace).st_mode & 0o777
        assert mode == 0o700, f"workspace mode is {oct(mode)}, expected 0o700"
    finally:
        import shutil

        shutil.rmtree(workspace, ignore_errors=True)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits not applicable on Windows")
def test_allocate_workspace_intermediate_dir_mode_0o700(make_worker):
    """OVH-062: the intermediate 'youtube' directory must also be 0o700."""
    worker = make_worker()
    workspace = worker._allocate_workspace()
    try:
        youtube_dir = workspace.parent
        mode = os.stat(youtube_dir).st_mode & 0o777
        assert mode == 0o700, f"youtube dir mode is {oct(mode)}, expected 0o700"
    finally:
        import shutil

        shutil.rmtree(workspace, ignore_errors=True)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits not applicable on Windows")
def test_allocate_workspace_tightens_preexisting_dir(make_worker, youtube_config, tmp_path):
    """OVH-062: an already-existing 'youtube' dir with loose permissions is tightened."""
    # Pre-create the directory with world-readable mode.
    youtube_dir = youtube_config.media_temp_folder / "youtube"
    youtube_dir.mkdir(parents=True, exist_ok=True)
    youtube_dir.chmod(0o755)

    worker = make_worker()
    workspace = worker._allocate_workspace()
    try:
        mode = os.stat(youtube_dir).st_mode & 0o777
        assert mode == 0o700, f"youtube dir mode after chmod: {oct(mode)}, expected 0o700"
    finally:
        import shutil

        shutil.rmtree(workspace, ignore_errors=True)


# ---------------------------------------------------------------------------
# processor_factory path — construction deferred to the worker thread
# ---------------------------------------------------------------------------


def test_factory_path_builds_processor_inside_run(qapp, youtube_config):
    """Given processor_factory and processor=None, run() builds the processor
    before mining, calls it, and curation_processor returns it."""
    built = MagicMock(name="EpisodeProcessor")
    built.process_youtube_url = MagicMock(return_value=MagicMock(name="ProcessingResult"))
    calls: list[int] = []

    def factory():
        calls.append(1)
        return built

    worker = YouTubeQueueWorker(
        processor=None,
        config=youtube_config,
        items=[_make_item()],
        curation_callback=None,
        preview_mode=False,
        processor_factory=factory,
    )
    assert worker.curation_processor is None

    caps = _connect_all(worker)
    worker.run()

    assert calls == [1]
    assert worker.curation_processor is built
    built.process_youtube_url.assert_called_once()
    assert len(caps["queue_finished"].calls) == 1


def test_factory_path_error_emits_error_and_queue_finished(qapp, youtube_config):
    """A factory that raises emits error + queue_finished and mines nothing."""

    def bad_factory():
        raise RuntimeError("registry scan failed")

    worker = YouTubeQueueWorker(
        processor=None,
        config=youtube_config,
        items=[_make_item()],
        curation_callback=None,
        preview_mode=False,
        processor_factory=bad_factory,
    )
    errors: list[str] = []
    worker.error.connect(errors.append)
    caps = _connect_all(worker)

    worker.run()

    assert len(errors) == 1
    assert "registry scan failed" in errors[0]
    assert caps["started"].calls == []
    assert len(caps["queue_finished"].calls) == 1


def test_prebuilt_processor_path_unchanged(make_worker, mock_processor):
    """When a processor is supplied directly, curation_processor returns it
    and process_youtube_url is driven by that instance."""
    worker = make_worker()
    worker.run()

    mock_processor.process_youtube_url.assert_called_once()
    assert worker.curation_processor is mock_processor


def test_both_processor_and_factory_raises(qapp, mock_processor, youtube_config):
    """Supplying both processor and processor_factory raises ValueError."""
    with pytest.raises(ValueError, match="not both"):
        YouTubeQueueWorker(
            processor=mock_processor,
            config=youtube_config,
            items=[_make_item()],
            curation_callback=None,
            preview_mode=False,
            processor_factory=lambda: mock_processor,
        )


def test_neither_processor_nor_factory_raises(qapp, youtube_config):
    """Supplying neither processor nor processor_factory raises ValueError."""
    with pytest.raises(ValueError, match="Either processor or processor_factory"):
        YouTubeQueueWorker(
            processor=None,
            config=youtube_config,
            items=[_make_item()],
            curation_callback=None,
            preview_mode=False,
        )


# ---------------------------------------------------------------------------
# 4.0: schema-staleness pre-loop gate — abort once, no per-item rows
# ---------------------------------------------------------------------------


def test_stale_dict_aborts_queue_once(make_worker, mock_processor):
    """A stale enabled dict slot surfaces the error exactly once (no per-item
    rows, no fetch/mine) and still emits queue_finished so the tab recovers."""
    from unittest.mock import patch

    items = [
        _make_item(url="https://www.youtube.com/watch?v=a", video_id="a"),
        _make_item(url="https://www.youtube.com/watch?v=b", video_id="b"),
    ]
    worker = make_worker(items=items)
    errors: list[str] = []
    worker.error.connect(errors.append)
    caps = _connect_all(worker)

    with patch(
        "anki_miner.gui.workers.youtube_queue_worker.stale_dict_reimport_error",
        return_value="Dictionary 'X' needs reimport (schema upgrade) — Settings → Dictionaries → Reimport All",
    ):
        worker.run()

    assert len(errors) == 1
    assert "Reimport All" in errors[0]
    assert caps["started"].calls == []
    assert caps["finished"].calls == []
    assert len(caps["queue_finished"].calls) == 1
    mock_processor.process_youtube_url.assert_not_called()
