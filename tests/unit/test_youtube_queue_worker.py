"""Tests for :class:`YouTubeQueueWorker`.

The queue worker drives a list of :class:`YouTubeQueueItem` through fetch +
mine sequentially with retry-once on :class:`YouTubeFetchError`. Tests
exercise the worker body synchronously by calling ``run()`` directly; Qt
threading itself is not under test.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QCoreApplication

from anki_miner.exceptions.youtube import YouTubeFetchError
from anki_miner.gui.workers.youtube_queue_worker import (
    YouTubeQueueWorker,
    _QueueMiningProgressAdapter,
)
from anki_miner.models.youtube_queue import YouTubeItemStatus, YouTubeQueueItem

# Qt needs a core application for signals. Created once per process.
_app = QCoreApplication.instance() or QCoreApplication([])


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
) -> YouTubeQueueItem:
    """Build a READY queue item with the given identity."""
    return YouTubeQueueItem(
        url=url,
        status=YouTubeItemStatus.READY,
        video_id=video_id,
        resolved_sub_mode=sub_mode,  # type: ignore[arg-type]
    )


@pytest.fixture
def make_worker(mock_processor, youtube_config):
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


def test_bot_detection_error_workspace_cleaned(mock_processor, youtube_config):
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
# preview_mode=False drops curation
# ---------------------------------------------------------------------------


def test_preview_mode_false_passes_none_curation(make_worker, mock_processor):
    def _curation(words):
        return words

    items = [_make_item(video_id="a")]
    worker = make_worker(items=items, curation_callback=_curation, preview_mode=False)
    worker.run()

    kwargs = mock_processor.process_youtube_url.call_args.kwargs
    assert kwargs["curation_callback"] is None
    assert kwargs["preview_mode"] is False


# ---------------------------------------------------------------------------
# preview_mode=True forwards curation
# ---------------------------------------------------------------------------


def test_preview_mode_true_forwards_curation_callback(make_worker, mock_processor):
    def _curation(words):
        return words

    items = [_make_item(video_id="a")]
    worker = make_worker(items=items, curation_callback=_curation, preview_mode=True)
    worker.run()

    kwargs = mock_processor.process_youtube_url.call_args.kwargs
    assert kwargs["curation_callback"] is _curation
    assert kwargs["preview_mode"] is True


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
        (7, "Extracting media: word-05", 50),
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

    # on_start emits pct=0; on_progress emits 1/1 = 100% under the clamp
    assert emitted[-1] == (3, "Edge case: item", 100)


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
