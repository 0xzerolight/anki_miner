"""Tests for :class:`YouTubePlaylistResolveWorker` and :class:`YouTubePlaylistProbeWorker`.

Both workers are exercised synchronously by calling ``run()`` directly; Qt
threading itself is not under test.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from PyQt6.QtCore import QCoreApplication

from anki_miner.exceptions.youtube import VideoTooLongError, YouTubeFetchError
from anki_miner.gui.workers.youtube_playlist_probe_worker import (
    YouTubePlaylistProbeWorker,
    YouTubePlaylistResolveWorker,
)
from anki_miner.models.youtube import PlaylistEntry, PlaylistInfo, VideoInfo

# Qt needs a core application for signals. Created once per process.
_app = QCoreApplication.instance() or QCoreApplication([])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_playlist_info(n: int = 3) -> PlaylistInfo:
    """Build a minimal PlaylistInfo with ``n`` entries."""
    entries = tuple(
        PlaylistEntry(
            video_id=f"v{i}",
            title=f"Video {i}",
            duration_s=60,
            url=f"https://www.youtube.com/watch?v=v{i}",
        )
        for i in range(n)
    )
    return PlaylistInfo(
        playlist_id="PLtest",
        title="Test Playlist",
        entries=entries,
        total_count=n,
    )


def _make_video_info(video_id: str = "abc") -> VideoInfo:
    """Build a minimal VideoInfo."""
    return VideoInfo(
        video_id=video_id,
        title=f"Title {video_id}",
        duration_s=120,
        has_manual_ja_subs=True,
        has_auto_ja_subs=False,
        thumbnail_url=None,
        uploader=None,
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
# YouTubePlaylistResolveWorker — success
# ---------------------------------------------------------------------------


def test_resolve_worker_success_emits_playlist_resolved() -> None:
    info = _make_playlist_info(5)
    fetcher = MagicMock()
    fetcher.probe_playlist.return_value = info

    worker = YouTubePlaylistResolveWorker(fetcher=fetcher, url="https://youtu.be/list=PL", limit=10)
    resolved = _SignalCapture()
    errored = _SignalCapture()
    worker.playlist_resolved.connect(resolved)
    worker.playlist_error.connect(errored)

    worker.run()

    assert resolved.calls == [(info,)]
    assert errored.calls == []
    fetcher.probe_playlist.assert_called_once_with("https://youtu.be/list=PL", 10, timeout_s=120.0)


# ---------------------------------------------------------------------------
# YouTubePlaylistResolveWorker — failure
# ---------------------------------------------------------------------------


def test_resolve_worker_exception_emits_playlist_error() -> None:
    fetcher = MagicMock()
    fetcher.probe_playlist.side_effect = YouTubeFetchError("timeout")

    worker = YouTubePlaylistResolveWorker(fetcher=fetcher, url="https://youtu.be/list=PL", limit=5)
    resolved = _SignalCapture()
    errored = _SignalCapture()
    worker.playlist_resolved.connect(resolved)
    worker.playlist_error.connect(errored)

    worker.run()

    assert resolved.calls == []
    assert len(errored.calls) == 1
    assert "timeout" in errored.calls[0][0]


def test_resolve_worker_generic_exception_emits_playlist_error() -> None:
    fetcher = MagicMock()
    fetcher.probe_playlist.side_effect = RuntimeError("unexpected")

    worker = YouTubePlaylistResolveWorker(fetcher=fetcher, url="https://youtu.be/list=PL", limit=5)
    errored = _SignalCapture()
    worker.playlist_error.connect(errored)

    worker.run()

    assert len(errored.calls) == 1
    assert "unexpected" in errored.calls[0][0]


# ---------------------------------------------------------------------------
# YouTubePlaylistResolveWorker — fetcher captured at construction
# ---------------------------------------------------------------------------


def test_resolve_worker_uses_fetcher_captured_at_construction() -> None:
    """The worker uses the fetcher passed to __init__, not any later re-assignment."""
    info = _make_playlist_info(2)
    fetcher_a = MagicMock()
    fetcher_a.probe_playlist.return_value = info
    fetcher_b = MagicMock()

    worker = YouTubePlaylistResolveWorker(fetcher=fetcher_a, url="u", limit=3)
    # Simulate tab rebuilding its fetcher attribute — worker must not notice.
    worker_fetcher_before_run = worker._fetcher
    worker.run()

    assert worker_fetcher_before_run is fetcher_a
    fetcher_a.probe_playlist.assert_called_once()
    fetcher_b.probe_playlist.assert_not_called()


# ---------------------------------------------------------------------------
# YouTubePlaylistResolveWorker — custom timeout_s forwarded
# ---------------------------------------------------------------------------


def test_resolve_worker_custom_timeout_forwarded() -> None:
    fetcher = MagicMock()
    fetcher.probe_playlist.return_value = _make_playlist_info(1)

    worker = YouTubePlaylistResolveWorker(fetcher=fetcher, url="u", limit=5, timeout_s=30.0)
    worker.run()

    fetcher.probe_playlist.assert_called_once_with("u", 5, timeout_s=30.0)


# ---------------------------------------------------------------------------
# YouTubePlaylistProbeWorker — all success
# ---------------------------------------------------------------------------


def test_probe_worker_all_success_emits_entry_probed_in_order() -> None:
    urls = [f"https://www.youtube.com/watch?v=v{i}" for i in range(3)]
    infos = [_make_video_info(f"v{i}") for i in range(3)]

    fetcher = MagicMock()
    fetcher.probe_metadata.side_effect = infos[:]

    worker = YouTubePlaylistProbeWorker(fetcher=fetcher, urls=urls)
    probed = _SignalCapture()
    failed = _SignalCapture()
    worker.entry_probed.connect(probed)
    worker.entry_failed.connect(failed)

    worker.run()

    assert failed.calls == []
    assert probed.calls == [(0, infos[0]), (1, infos[1]), (2, infos[2])]


# ---------------------------------------------------------------------------
# YouTubePlaylistProbeWorker — one failure continues
# ---------------------------------------------------------------------------


def test_probe_worker_one_failure_emits_entry_failed_and_continues() -> None:
    urls = [
        "https://www.youtube.com/watch?v=a",
        "https://www.youtube.com/watch?v=b",
        "https://www.youtube.com/watch?v=c",
    ]
    info_a = _make_video_info("a")
    info_c = _make_video_info("c")

    def _side_effect(url: str, timeout_s: float) -> VideoInfo:
        if url.endswith("=b"):
            raise YouTubeFetchError("b failed")
        return info_a if url.endswith("=a") else info_c

    fetcher = MagicMock()
    fetcher.probe_metadata.side_effect = _side_effect

    worker = YouTubePlaylistProbeWorker(fetcher=fetcher, urls=urls)
    probed = _SignalCapture()
    failed = _SignalCapture()
    worker.entry_probed.connect(probed)
    worker.entry_failed.connect(failed)

    worker.run()

    # idx=1 failed; idx=0 and idx=2 succeeded
    assert probed.calls == [(0, info_a), (2, info_c)]
    assert len(failed.calls) == 1
    assert failed.calls[0][0] == 1  # correct index
    assert "b failed" in failed.calls[0][1]


# ---------------------------------------------------------------------------
# YouTubePlaylistProbeWorker — cancel before run
# ---------------------------------------------------------------------------


def test_probe_worker_cancel_before_run_emits_nothing() -> None:
    fetcher = MagicMock()
    fetcher.probe_metadata.return_value = _make_video_info("x")

    urls = ["https://www.youtube.com/watch?v=x", "https://www.youtube.com/watch?v=y"]
    worker = YouTubePlaylistProbeWorker(fetcher=fetcher, urls=urls)
    worker.cancel()

    probed = _SignalCapture()
    failed = _SignalCapture()
    worker.entry_probed.connect(probed)
    worker.entry_failed.connect(failed)

    worker.run()

    assert probed.calls == []
    assert failed.calls == []
    fetcher.probe_metadata.assert_not_called()


# ---------------------------------------------------------------------------
# YouTubePlaylistProbeWorker — cancel mid-run stops further probes
# ---------------------------------------------------------------------------


def test_probe_worker_cancel_mid_run_stops_after_first_entry() -> None:
    """Cancelling inside the first probe_metadata call stops before the second entry."""
    urls = [
        "https://www.youtube.com/watch?v=first",
        "https://www.youtube.com/watch?v=second",
        "https://www.youtube.com/watch?v=third",
    ]
    info_first = _make_video_info("first")

    worker_ref: list[YouTubePlaylistProbeWorker] = []

    def _probe_and_maybe_cancel(url: str, timeout_s: float) -> VideoInfo:
        if "first" in url:
            # Simulate the tab (or test) cancelling after the first probe completes.
            worker_ref[0].cancel()
            return info_first
        raise AssertionError("should not be called after cancel")

    fetcher = MagicMock()
    fetcher.probe_metadata.side_effect = _probe_and_maybe_cancel

    worker = YouTubePlaylistProbeWorker(fetcher=fetcher, urls=urls)
    worker_ref.append(worker)

    probed = _SignalCapture()
    failed = _SignalCapture()
    worker.entry_probed.connect(probed)
    worker.entry_failed.connect(failed)

    worker.run()

    # Only the first entry was probed before cancel took effect.
    assert probed.calls == [(0, info_first)]
    assert failed.calls == []
    assert fetcher.probe_metadata.call_count == 1


# ---------------------------------------------------------------------------
# YouTubePlaylistProbeWorker — fetcher captured at construction
# ---------------------------------------------------------------------------


def test_probe_worker_uses_fetcher_captured_at_construction() -> None:
    info = _make_video_info("z")
    fetcher_a = MagicMock()
    fetcher_a.probe_metadata.return_value = info
    fetcher_b = MagicMock()

    worker = YouTubePlaylistProbeWorker(fetcher=fetcher_a, urls=["https://www.youtube.com/watch?v=z"])
    captured = worker._fetcher
    worker.run()

    assert captured is fetcher_a
    fetcher_a.probe_metadata.assert_called_once()
    fetcher_b.probe_metadata.assert_not_called()


# ---------------------------------------------------------------------------
# YouTubePlaylistProbeWorker — empty url list
# ---------------------------------------------------------------------------


def test_probe_worker_empty_urls_emits_nothing() -> None:
    fetcher = MagicMock()
    worker = YouTubePlaylistProbeWorker(fetcher=fetcher, urls=[])

    probed = _SignalCapture()
    failed = _SignalCapture()
    worker.entry_probed.connect(probed)
    worker.entry_failed.connect(failed)

    worker.run()

    assert probed.calls == []
    assert failed.calls == []
    fetcher.probe_metadata.assert_not_called()


# ---------------------------------------------------------------------------
# YouTubePlaylistProbeWorker — custom timeout_s forwarded per call
# ---------------------------------------------------------------------------


def test_probe_worker_custom_timeout_forwarded_to_each_probe() -> None:
    urls = ["https://www.youtube.com/watch?v=a", "https://www.youtube.com/watch?v=b"]
    fetcher = MagicMock()
    fetcher.probe_metadata.side_effect = [_make_video_info("a"), _make_video_info("b")]

    worker = YouTubePlaylistProbeWorker(fetcher=fetcher, urls=urls, timeout_s=30.0)
    worker.run()

    for call in fetcher.probe_metadata.call_args_list:
        assert call.kwargs["timeout_s"] == 30.0


# ---------------------------------------------------------------------------
# YouTubePlaylistProbeWorker — generic exception surfaced as entry_failed
# ---------------------------------------------------------------------------


def test_probe_worker_generic_exception_emits_entry_failed() -> None:
    fetcher = MagicMock()
    fetcher.probe_metadata.side_effect = ValueError("json parse error")

    worker = YouTubePlaylistProbeWorker(fetcher=fetcher, urls=["https://www.youtube.com/watch?v=x"])
    failed = _SignalCapture()
    worker.entry_failed.connect(failed)

    worker.run()

    assert len(failed.calls) == 1
    assert failed.calls[0][0] == 0
    assert "json parse error" in failed.calls[0][1]


def test_probe_worker_video_too_long_emits_entry_failed_and_continues() -> None:
    """An over-long video becomes entry_failed; the rest of the playlist still probes."""
    info = _make_video_info("ok_video_id")
    fetcher = MagicMock()
    fetcher.probe_metadata.side_effect = [
        VideoTooLongError("video exceeds maximum duration"),
        info,
    ]

    worker = YouTubePlaylistProbeWorker(
        fetcher=fetcher,
        urls=[
            "https://www.youtube.com/watch?v=toolongvid1",
            "https://www.youtube.com/watch?v=ok_video_id",
        ],
    )
    probed = _SignalCapture()
    failed = _SignalCapture()
    worker.entry_probed.connect(probed)
    worker.entry_failed.connect(failed)

    worker.run()

    assert len(failed.calls) == 1
    assert failed.calls[0][0] == 0
    assert "maximum duration" in failed.calls[0][1]
    assert probed.calls == [(1, info)]
