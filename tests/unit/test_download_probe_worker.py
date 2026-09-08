"""Off-thread probes backing the Download tool's pickers."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.exceptions import OperationCancelled  # noqa: E402
from anki_miner.gui.workers.download_probe_worker import (  # noqa: E402
    DownloadPlaylistResolveWorker,
    DownloadTracksProbeWorker,
)
from anki_miner.services.media_downloader import (  # noqa: E402
    DownloadPlaylist,
    MediaDownloadError,
    UrlTracks,
)

_TRACKS = UrlTracks("T", ("ja",), (), ("ja", "en"), False)
_PLAYLIST = DownloadPlaylist("L", (), 0)


def _run(worker: Any, qtbot: Any, signal: Any) -> list[Any]:
    with qtbot.waitSignal(signal, timeout=5000) as blocker:
        worker.start()
    worker.wait(5000)
    return list(blocker.args)


def test_tracks_probe_emits_the_service_result(qtbot: Any) -> None:
    service = MagicMock()
    service.probe_tracks.return_value = _TRACKS
    worker = DownloadTracksProbeWorker(service, "https://example.com/v")
    assert _run(worker, qtbot, worker.tracks_probed) == [_TRACKS]
    service.probe_tracks.assert_called_once_with(
        "https://example.com/v", timeout_s=120.0, cancel_event=worker._cancel_event
    )


def test_tracks_probe_reports_a_service_failure_as_an_error_string(qtbot: Any) -> None:
    service = MagicMock()
    service.probe_tracks.side_effect = MediaDownloadError("boom")
    worker = DownloadTracksProbeWorker(service, "https://example.com/v")
    (message,) = _run(worker, qtbot, worker.probe_error)
    assert "boom" in message


def test_playlist_resolve_passes_the_limit_through(qtbot: Any) -> None:
    service = MagicMock()
    service.probe_playlist.return_value = _PLAYLIST
    worker = DownloadPlaylistResolveWorker(service, "https://example.com/l", limit=50)
    assert _run(worker, qtbot, worker.playlist_resolved) == [_PLAYLIST]
    service.probe_playlist.assert_called_once_with(
        "https://example.com/l", limit=50, timeout_s=120.0, start=1, cancel_event=worker._cancel_event
    )


def test_playlist_resolve_passes_start_through(qtbot: Any) -> None:
    service = MagicMock()
    service.probe_playlist.return_value = _PLAYLIST
    worker = DownloadPlaylistResolveWorker(service, "https://example.com/l", limit=50, start=51)
    _run(worker, qtbot, worker.playlist_resolved)
    assert service.probe_playlist.call_args.kwargs["start"] == 51


def test_playlist_resolve_reports_a_failure(qtbot: Any) -> None:
    service = MagicMock()
    service.probe_playlist.side_effect = MediaDownloadError("not a playlist")
    worker = DownloadPlaylistResolveWorker(service, "https://example.com/v", limit=50)
    (message,) = _run(worker, qtbot, worker.probe_error)
    assert "not a playlist" in message


def test_an_unexpected_exception_still_reaches_the_error_signal(qtbot: Any) -> None:
    """report_failure is the one catch-all: a worker must never die silently."""
    service = MagicMock()
    service.probe_tracks.side_effect = RuntimeError("unexpected")
    worker = DownloadTracksProbeWorker(service, "https://example.com/v")
    (message,) = _run(worker, qtbot, worker.probe_error)
    assert message


def test_cancel_reaches_the_service_call(qtbot: Any) -> None:
    service = MagicMock()
    service.probe_tracks.return_value = _TRACKS
    worker = DownloadTracksProbeWorker(service, "https://example.com/v")
    worker.cancel()
    _run(worker, qtbot, worker.tracks_probed)
    event = service.probe_tracks.call_args.kwargs["cancel_event"]
    assert event is worker._cancel_event
    assert event.is_set()


def test_a_cancelled_probe_is_not_reported_as_an_error(qtbot: Any) -> None:
    """report_failure files OperationCancelled as INFO: no probe_error, no screen issue."""
    service = MagicMock()
    service.probe_tracks.side_effect = OperationCancelled("cancelled")
    worker = DownloadTracksProbeWorker(service, "https://example.com/v")
    errors: list[str] = []
    worker.probe_error.connect(errors.append)
    with qtbot.waitSignal(worker.finished, timeout=5000):
        worker.start()
    worker.wait(5000)
    assert errors == []
