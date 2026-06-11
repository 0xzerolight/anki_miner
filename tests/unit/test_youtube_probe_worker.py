"""Tests for :class:`YouTubeProbeWorker` (single-video metadata probe).

Ported from the playlist sibling's suite shape. ``YouTubeProbeWorker`` and
``YouTubePlaylistResolveWorker`` share
:class:`~anki_miner.gui.workers.youtube_probe_worker._SingleCallProbeThread`;
its run() try/else body (emit-result-on-success, emit-error-on-exception) is
exercised here through the concrete probe worker, while the playlist test
covers the resolve worker. Per-worker signal payloads (``probe_done`` /
``probe_error``) and the ``timeout_s`` forwarding are pinned below.

Exercised synchronously by calling ``run()`` directly; Qt threading itself is
not under test.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from PyQt6.QtWidgets import QApplication

from anki_miner.exceptions.youtube import VideoTooLongError, YouTubeFetchError
from anki_miner.gui.workers.youtube_probe_worker import (
    YouTubeProbeWorker,
    _SingleCallProbeThread,
)
from anki_miner.models.youtube import VideoInfo

# Must be QApplication, not QCoreApplication: the app object is a process-wide
# singleton shared with widget tests. A bare QCoreApplication satisfies signals
# here but poisons QApplication.instance() for any widget test that imports
# later in the same process, aborting QWidget construction ("Cannot create a
# QWidget without QApplication"). QApplication is a QCoreApplication, so signals
# still work. Created once per process.
_app = QApplication.instance() or QApplication([])


def _make_video_info(video_id: str = "abc") -> VideoInfo:
    """Build a minimal VideoInfo."""
    return VideoInfo(
        video_id=video_id,
        title=f"Title {video_id}",
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

    def __call__(self, *args: object) -> None:
        self.calls.append(args)


# ---------------------------------------------------------------------------
# Shared body wiring
# ---------------------------------------------------------------------------


def test_probe_worker_is_single_call_probe_thread() -> None:
    """YouTubeProbeWorker rides the shared _SingleCallProbeThread body."""
    worker = YouTubeProbeWorker(fetcher=MagicMock(), url="u")
    assert isinstance(worker, _SingleCallProbeThread)


# ---------------------------------------------------------------------------
# Success
# ---------------------------------------------------------------------------


def test_probe_worker_success_emits_probe_done() -> None:
    info = _make_video_info("vid1")
    fetcher = MagicMock()
    fetcher.probe_metadata.return_value = info

    worker = YouTubeProbeWorker(fetcher=fetcher, url="https://youtu.be/vid1")
    done = _SignalCapture()
    errored = _SignalCapture()
    worker.probe_done.connect(done)
    worker.probe_error.connect(errored)

    worker.run()

    assert done.calls == [(info,)]
    assert errored.calls == []
    fetcher.probe_metadata.assert_called_once_with("https://youtu.be/vid1", timeout_s=60.0)


# ---------------------------------------------------------------------------
# Failure branches (the shared run()'s except path)
# ---------------------------------------------------------------------------


def test_probe_worker_fetch_error_emits_probe_error() -> None:
    fetcher = MagicMock()
    fetcher.probe_metadata.side_effect = YouTubeFetchError("timeout")

    worker = YouTubeProbeWorker(fetcher=fetcher, url="u")
    done = _SignalCapture()
    errored = _SignalCapture()
    worker.probe_done.connect(done)
    worker.probe_error.connect(errored)

    worker.run()

    assert done.calls == []
    assert len(errored.calls) == 1
    assert "timeout" in errored.calls[0][0]


def test_probe_worker_video_too_long_emits_probe_error() -> None:
    """VideoTooLongError (a fetch-time guard) surfaces as probe_error."""
    fetcher = MagicMock()
    fetcher.probe_metadata.side_effect = VideoTooLongError("video exceeds maximum duration")

    worker = YouTubeProbeWorker(fetcher=fetcher, url="u")
    errored = _SignalCapture()
    worker.probe_error.connect(errored)

    worker.run()

    assert len(errored.calls) == 1
    assert "maximum duration" in errored.calls[0][0]


def test_probe_worker_generic_exception_emits_probe_error() -> None:
    fetcher = MagicMock()
    fetcher.probe_metadata.side_effect = RuntimeError("json parse error")

    worker = YouTubeProbeWorker(fetcher=fetcher, url="u")
    done = _SignalCapture()
    errored = _SignalCapture()
    worker.probe_done.connect(done)
    worker.probe_error.connect(errored)

    worker.run()

    assert done.calls == []
    assert len(errored.calls) == 1
    assert "json parse error" in errored.calls[0][0]


# ---------------------------------------------------------------------------
# Fetcher captured at construction
# ---------------------------------------------------------------------------


def test_probe_worker_uses_fetcher_captured_at_construction() -> None:
    """The worker uses the fetcher passed to __init__, not any later swap."""
    info = _make_video_info("z")
    fetcher_a = MagicMock()
    fetcher_a.probe_metadata.return_value = info
    fetcher_b = MagicMock()

    worker = YouTubeProbeWorker(fetcher=fetcher_a, url="https://youtu.be/z")
    captured = worker._fetcher
    worker.run()

    assert captured is fetcher_a
    fetcher_a.probe_metadata.assert_called_once()
    fetcher_b.probe_metadata.assert_not_called()


# ---------------------------------------------------------------------------
# Custom timeout_s forwarded
# ---------------------------------------------------------------------------


def test_probe_worker_custom_timeout_forwarded() -> None:
    fetcher = MagicMock()
    fetcher.probe_metadata.return_value = _make_video_info("a")

    worker = YouTubeProbeWorker(fetcher=fetcher, url="u", timeout_s=15.0)
    worker.run()

    fetcher.probe_metadata.assert_called_once_with("u", timeout_s=15.0)


def test_probe_worker_default_timeout_is_60() -> None:
    fetcher = MagicMock()
    fetcher.probe_metadata.return_value = _make_video_info("a")

    worker = YouTubeProbeWorker(fetcher=fetcher, url="u")
    worker.run()

    assert fetcher.probe_metadata.call_args.kwargs["timeout_s"] == 60.0
