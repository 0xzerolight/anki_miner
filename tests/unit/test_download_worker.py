"""Tests for DownloadWorker — signal sequence, skip mapping, per-item error
isolation, fatal yt-dlp-missing queue stop, and cancel semantics.

The service is a MagicMock; ``worker.run()`` is called synchronously
(pattern: ``tests/unit/test_condense_worker.py``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtCore")

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions.youtube import YtdlpNotFoundError
from anki_miner.gui.workers.download_worker import DownloadWorker
from anki_miner.models.processing import TerminalOutcome
from anki_miner.services.media_downloader import (
    DownloadOptions,
    DownloadResult,
    DownloadStatus,
    MediaDownloadError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _opts() -> DownloadOptions:
    return DownloadOptions(format_selector="bestvideo*+bestaudio/best")


def _make_worker(
    tmp_path: Path,
    urls: list[str],
    service: Any,
) -> DownloadWorker:
    config = AnkiMinerConfig(media_temp_folder=tmp_path / "temp")
    return DownloadWorker(
        config,
        urls,
        dest_dir=tmp_path / "downloads",
        options=_opts(),
        service=service,
    )


class _Recorder:
    """Collects every signal emission from a worker, in order."""

    def __init__(self, worker: DownloadWorker) -> None:
        self.events: list[tuple[str, tuple[Any, ...]]] = []
        worker.file_started.connect(lambda *a: self.events.append(("started", a)))
        worker.file_progress.connect(lambda *a: self.events.append(("progress", a)))
        worker.file_finished.connect(lambda *a: self.events.append(("finished", a)))
        worker.file_skipped.connect(lambda *a: self.events.append(("skipped", a)))
        worker.queue_finished.connect(lambda *a: self.events.append(("queue_finished", a)))
        worker.error.connect(lambda *a: self.events.append(("error", a)))

    def of(self, kind: str) -> list[tuple[Any, ...]]:
        return [args for name, args in self.events if name == kind]

    @property
    def outcome(self) -> TerminalOutcome:
        return self.of("queue_finished")[0][0]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_happy_path_signal_sequence(tmp_path: Path) -> None:
    service = MagicMock()
    service.download.return_value = DownloadResult(DownloadStatus.DONE, Path("out.mp4"))
    worker = _make_worker(tmp_path, ["https://a", "https://b"], service)
    rec = _Recorder(worker)

    worker.run()

    assert [args[0] for args in rec.of("started")] == [0, 1]
    finished = rec.of("finished")
    assert finished == [(0, Path("out.mp4"), None), (1, Path("out.mp4"), None)]
    assert rec.outcome is TerminalOutcome.SUCCESS
    assert service.download.call_count == 2


def test_dest_dir_created(tmp_path: Path) -> None:
    service = MagicMock()
    service.download.return_value = DownloadResult(DownloadStatus.DONE, None)
    worker = _make_worker(tmp_path, ["https://a"], service)

    worker.run()

    assert (tmp_path / "downloads").is_dir()


def test_already_downloaded_emits_skip(tmp_path: Path) -> None:
    service = MagicMock()
    service.download.return_value = DownloadResult(DownloadStatus.ALREADY_DOWNLOADED, Path("v.mp4"))
    worker = _make_worker(tmp_path, ["https://a"], service)
    rec = _Recorder(worker)

    worker.run()

    skipped = rec.of("skipped")
    assert len(skipped) == 1
    assert skipped[0][0] == 0
    assert skipped[0][1] == Path("v.mp4")
    assert rec.of("finished") == []
    assert rec.outcome is TerminalOutcome.SUCCESS


def test_per_item_isolation(tmp_path: Path) -> None:
    service = MagicMock()
    service.download.side_effect = [
        MediaDownloadError("boom"),
        DownloadResult(DownloadStatus.DONE, Path("ok.mp4")),
    ]
    worker = _make_worker(tmp_path, ["https://bad", "https://good"], service)
    rec = _Recorder(worker)

    worker.run()

    finished = rec.of("finished")
    assert finished[0][0] == 0
    assert finished[0][1] is None
    assert "boom" in finished[0][2]
    assert finished[1] == (1, Path("ok.mp4"), None)
    assert rec.outcome is TerminalOutcome.PARTIAL


def test_ytdlp_missing_stops_queue_not_cancelled(tmp_path: Path) -> None:
    service = MagicMock()
    service.download.side_effect = YtdlpNotFoundError("no binary")
    worker = _make_worker(tmp_path, ["https://a", "https://b", "https://c"], service)
    rec = _Recorder(worker)

    worker.run()

    assert service.download.call_count == 1
    finished = rec.of("finished")
    assert len(finished) == 1
    assert "no binary" in finished[0][2]
    assert worker.is_cancelled is False
    assert rec.outcome is TerminalOutcome.FAILED


def test_cancel_mid_item(tmp_path: Path) -> None:
    service = MagicMock()

    def _cancelling_download(*_a: Any, **_k: Any) -> DownloadResult:
        worker.cancel()
        return DownloadResult(DownloadStatus.CANCELLED, None)

    service.download.side_effect = _cancelling_download
    worker = _make_worker(tmp_path, ["https://a", "https://b"], service)
    rec = _Recorder(worker)

    worker.run()

    assert service.download.call_count == 1
    finished = rec.of("finished")
    assert len(finished) == 1
    assert finished[0][1] is None
    assert rec.outcome is TerminalOutcome.CANCELLED


def test_progress_fraction_mapping(tmp_path: Path) -> None:
    service = MagicMock()

    def _download(url: str, dest: Path, options: DownloadOptions, **kwargs: Any) -> DownloadResult:
        progress_cb = kwargs["progress_cb"]
        progress_cb("Downloading", 0.25)
        progress_cb("Downloading", None)
        return DownloadResult(DownloadStatus.DONE, None)

    service.download.side_effect = _download
    worker = _make_worker(tmp_path, ["https://a"], service)
    rec = _Recorder(worker)

    worker.run()

    progress = rec.of("progress")
    assert (0, 25, "Downloading: 25%") in progress
    assert (0, 0, "Downloading") in progress


def test_progress_message_carries_percent(tmp_path: Path) -> None:
    service = MagicMock()

    def _download(url: str, dest: Path, options: DownloadOptions, **kwargs: Any) -> DownloadResult:
        kwargs["progress_cb"]("Downloading video", 0.42)
        return DownloadResult(DownloadStatus.DONE, None)

    service.download.side_effect = _download
    worker = _make_worker(tmp_path, ["https://a"], service)
    rec = _Recorder(worker)

    worker.run()

    idx, pct, message = rec.of("progress")[0]
    assert idx == 0
    assert pct == 42
    assert "42" in message


def test_progress_message_without_fraction_stays_bare(tmp_path: Path) -> None:
    service = MagicMock()

    def _download(url: str, dest: Path, options: DownloadOptions, **kwargs: Any) -> DownloadResult:
        kwargs["progress_cb"]("Merging audio and video", None)
        return DownloadResult(DownloadStatus.DONE, None)

    service.download.side_effect = _download
    worker = _make_worker(tmp_path, ["https://a"], service)
    rec = _Recorder(worker)

    worker.run()

    _idx, _pct, message = rec.of("progress")[0]
    assert "%" not in message


def test_cancel_event_passed_to_service(tmp_path: Path) -> None:
    service = MagicMock()
    service.download.return_value = DownloadResult(DownloadStatus.DONE, None)
    worker = _make_worker(tmp_path, ["https://a"], service)

    worker.run()

    assert service.download.call_args.kwargs["cancel_event"] is worker._cancel_event
