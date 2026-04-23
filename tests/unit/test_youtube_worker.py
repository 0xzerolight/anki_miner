"""Tests for :class:`YouTubeWorkerThread`.

These tests exercise the worker's thread-body logic synchronously (calling
``run()`` directly) rather than via ``start()``. Qt threading itself is not
under test — only workspace lifecycle, cancellation propagation, progress
translation, and signal emission shape are.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QCoreApplication

from anki_miner.gui.workers.youtube_worker import (
    YouTubeWorkerThread,
    _MiningProgressAdapter,
)

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
    """MagicMock stand-in for EpisodeProcessor.

    ``process_youtube_url`` is not yet defined on the real class at time of
    writing (parallel agent adds it). Mocks bypass attribute checks so this
    works regardless.
    """
    processor = MagicMock()
    processor.process_youtube_url = MagicMock(return_value=MagicMock(name="ProcessingResult"))
    return processor


@pytest.fixture
def make_worker(mock_processor, youtube_config):
    """Factory producing a YouTubeWorkerThread with sensible defaults."""

    def _make(
        url: str = "https://www.youtube.com/watch?v=abc123",
        video_id: str = "abc123",
        sub_mode: str = "manual_only",
    ) -> YouTubeWorkerThread:
        return YouTubeWorkerThread(
            processor=mock_processor,
            config=youtube_config,
            url=url,
            video_id=video_id,
            sub_mode=sub_mode,  # type: ignore[arg-type]
        )

    return _make


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path_creates_workspace_calls_processor_emits_result(
    make_worker, mock_processor, youtube_config
):
    worker = make_worker()
    observed_workspaces: list = []

    def _capture_workspace(**kwargs):
        observed_workspaces.append(kwargs["workspace"])
        # Workspace must exist at call time.
        assert kwargs["workspace"].is_dir()
        return "RESULT"

    mock_processor.process_youtube_url.side_effect = _capture_workspace

    result_capture = _SignalCapture()
    error_capture = _SignalCapture()
    worker.result_ready.connect(result_capture)
    worker.error.connect(error_capture)

    worker.run()

    assert mock_processor.process_youtube_url.call_count == 1
    kwargs = mock_processor.process_youtube_url.call_args.kwargs
    assert kwargs["url"] == "https://www.youtube.com/watch?v=abc123"
    assert kwargs["video_id"] == "abc123"
    assert kwargs["sub_mode"] == "manual_only"
    assert kwargs["cancel_event"] is worker._cancel_event

    # Workspace path shape: <media_temp>/youtube/run-<hex>
    ws = observed_workspaces[0]
    assert ws.parent == youtube_config.media_temp_folder / "youtube"
    assert ws.name.startswith("run-")

    # result_ready fired once; error never.
    assert len(result_capture.calls) == 1
    assert result_capture.calls[0] == ("RESULT",)
    assert error_capture.calls == []

    # Workspace cleaned up in finally.
    assert not ws.exists()


# ---------------------------------------------------------------------------
# Processor raises
# ---------------------------------------------------------------------------


def test_processor_exception_emits_error_and_cleans_workspace(make_worker, mock_processor):
    worker = make_worker()
    mock_processor.process_youtube_url.side_effect = RuntimeError("download failed")

    captured_ws: list = []
    orig = mock_processor.process_youtube_url.side_effect

    def _wrapped(**kwargs):
        captured_ws.append(kwargs["workspace"])
        raise orig

    mock_processor.process_youtube_url.side_effect = _wrapped

    result_capture = _SignalCapture()
    error_capture = _SignalCapture()
    worker.result_ready.connect(result_capture)
    worker.error.connect(error_capture)

    worker.run()

    assert result_capture.calls == []
    assert len(error_capture.calls) == 1
    assert error_capture.calls[0] == ("download failed",)

    assert captured_ws and not captured_ws[0].exists()


# ---------------------------------------------------------------------------
# Cancelled before run
# ---------------------------------------------------------------------------


def test_cancelled_before_run_skips_processor_and_workspace(
    make_worker, mock_processor, youtube_config
):
    worker = make_worker()
    worker.cancel()

    result_capture = _SignalCapture()
    error_capture = _SignalCapture()
    worker.result_ready.connect(result_capture)
    worker.error.connect(error_capture)

    worker.run()

    assert mock_processor.process_youtube_url.call_count == 0
    assert result_capture.calls == []
    assert error_capture.calls == []

    # No workspace should have been created under youtube/.
    yt_root = youtube_config.media_temp_folder / "youtube"
    assert not yt_root.exists() or not any(yt_root.iterdir())


# ---------------------------------------------------------------------------
# Cancel during run
# ---------------------------------------------------------------------------


def test_cancel_during_run_suppresses_result(make_worker, mock_processor):
    worker = make_worker()

    def _cancel_midway(**kwargs):
        kwargs["cancel_event"].set()
        return MagicMock(name="PartialResult")

    mock_processor.process_youtube_url.side_effect = _cancel_midway

    result_capture = _SignalCapture()
    error_capture = _SignalCapture()
    worker.result_ready.connect(result_capture)
    worker.error.connect(error_capture)

    worker.run()

    assert mock_processor.process_youtube_url.call_count == 1
    # Result suppressed because worker saw cancellation after the call returned.
    assert result_capture.calls == []
    assert error_capture.calls == []


# ---------------------------------------------------------------------------
# Cancel forwarded to processor
# ---------------------------------------------------------------------------


def test_cancel_event_identity_forwarded_to_processor(make_worker, mock_processor):
    worker = make_worker()
    worker.run()

    kwargs = mock_processor.process_youtube_url.call_args.kwargs
    assert kwargs["cancel_event"] is worker._cancel_event


# ---------------------------------------------------------------------------
# Progress emit shape
# ---------------------------------------------------------------------------


def test_progress_emit_determinate_fraction(make_worker):
    worker = make_worker()
    capture = _SignalCapture()
    worker.progress.connect(capture)

    worker._emit_progress("Downloading", 0.5)

    assert capture.calls == [("Downloading", 50)]


def test_progress_emit_indeterminate_frac_none(make_worker):
    worker = make_worker()
    capture = _SignalCapture()
    worker.progress.connect(capture)

    worker._emit_progress("Merging", None)

    assert capture.calls == [("Merging", -1)]


def test_progress_emit_clamps_out_of_range(make_worker):
    worker = make_worker()
    capture = _SignalCapture()
    worker.progress.connect(capture)

    worker._emit_progress("Downloading", 1.05)
    worker._emit_progress("Downloading", -0.1)

    assert capture.calls == [("Downloading", 100), ("Downloading", 0)]


def test_progress_callbacks_wired_into_processor(make_worker, mock_processor):
    worker = make_worker()
    worker.run()
    kwargs = mock_processor.process_youtube_url.call_args.kwargs

    # Fetch phase gets the raw (label, frac) callable so yt-dlp progress
    # hooks can push directly into the Qt progress signal.
    assert kwargs["fetch_progress_cb"] == worker._emit_progress

    # Mining phase gets a ProgressCallback-protocol object. Services call
    # on_start/on_progress/on_complete/on_error; bare callable would raise
    # AttributeError. Verify all four methods are present.
    mining_cb = kwargs["progress_callback"]
    assert mining_cb is not worker._emit_progress
    for attr in ("on_start", "on_progress", "on_complete", "on_error"):
        assert callable(getattr(mining_cb, attr)), f"missing {attr}"


# ---------------------------------------------------------------------------
# _MiningProgressAdapter translates ProgressCallback into (label, pct) tuples
# ---------------------------------------------------------------------------


def test_mining_progress_adapter_translates_start_progress_complete():
    emitted: list[tuple[str, int]] = []
    adapter = _MiningProgressAdapter(lambda label, pct: emitted.append((label, pct)))

    adapter.on_start(10, "Extracting media")
    adapter.on_progress(5, "word-05")
    adapter.on_progress(10, "word-10")
    adapter.on_complete()

    assert emitted == [
        ("Extracting media", 0),
        ("Extracting media: word-05", 50),
        ("Extracting media: word-10", 100),
        ("Extracting media", 100),
    ]


def test_mining_progress_adapter_on_error_emits_nothing():
    """on_error must not emit progress.

    Per-item mining failures surface as exceptions that the worker's except
    clause routes to the `error` signal. An indeterminate progress emit here
    would re-trigger the widget's busy animation after mining failed — the
    exact bug this no-op prevents.
    """
    emitted: list[tuple[str, int]] = []
    adapter = _MiningProgressAdapter(lambda label, pct: emitted.append((label, pct)))

    adapter.on_error("word-11", "boom")

    assert emitted == []


def test_mining_progress_adapter_handles_zero_total():
    """``total=0`` must not divide-by-zero when on_progress fires."""
    emitted: list[tuple[str, int]] = []
    adapter = _MiningProgressAdapter(lambda label, pct: emitted.append((label, pct)))

    adapter.on_start(0, "Edge case")
    adapter.on_progress(1, "item")

    # total clamped to 1 internally, so 1/1 = 100%
    assert emitted[-1] == ("Edge case: item", 100)


# ---------------------------------------------------------------------------
# Curation callback and preview mode flow through to the processor
# ---------------------------------------------------------------------------


def test_curation_and_preview_mode_forwarded(mock_processor, youtube_config):
    """ctor kwargs for curation/preview reach process_youtube_url unchanged."""
    curation_cb = lambda words: words  # noqa: E731 - identity fn for call capture
    worker = YouTubeWorkerThread(
        processor=mock_processor,
        config=youtube_config,
        url="https://www.youtube.com/watch?v=abc123",
        video_id="abc123",
        sub_mode="manual_only",  # type: ignore[arg-type]
        curation_callback=curation_cb,
        preview_mode=True,
    )
    worker.run()

    kwargs = mock_processor.process_youtube_url.call_args.kwargs
    assert kwargs["curation_callback"] is curation_cb
    assert kwargs["preview_mode"] is True


def test_curation_and_preview_default_when_omitted(make_worker, mock_processor):
    """When ctor args are omitted, None/False are forwarded."""
    worker = make_worker()
    worker.run()

    kwargs = mock_processor.process_youtube_url.call_args.kwargs
    assert kwargs["curation_callback"] is None
    assert kwargs["preview_mode"] is False


# ---------------------------------------------------------------------------
# Workspace directory name format
# ---------------------------------------------------------------------------


def test_workspace_name_matches_run_hex_format(make_worker, mock_processor):
    worker = make_worker()
    observed: list = []

    def _record(**kwargs):
        observed.append(kwargs["workspace"])
        return MagicMock()

    mock_processor.process_youtube_url.side_effect = _record
    worker.run()

    name = observed[0].name
    assert name.startswith("run-")
    hex_part = name[len("run-") :]
    assert len(hex_part) == 32  # UUID4 hex
    int(hex_part, 16)  # must parse as hex, else raises ValueError


# ---------------------------------------------------------------------------
# rmtree robustness across exception types
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [RuntimeError("boom"), KeyboardInterrupt(), ValueError("bad"), OSError("io")],
)
def test_rmtree_runs_on_every_exception(make_worker, mock_processor, exc):
    worker = make_worker()
    captured: list = []

    def _raise(**kwargs):
        captured.append(kwargs["workspace"])
        raise exc

    mock_processor.process_youtube_url.side_effect = _raise

    # KeyboardInterrupt is NOT caught by ``except Exception`` — it propagates.
    # The finally block must still run regardless.
    if isinstance(exc, KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            worker.run()
    else:
        worker.run()

    assert captured and not captured[0].exists()
