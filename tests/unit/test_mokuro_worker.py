"""Tests for MokuroWorker — signals, skip rule, per-item isolation, fatal stop, cancel."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtCore")

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions.mokuro import MokuroError, MokuroNotFoundError
from anki_miner.gui.workers.mokuro_worker import MokuroWorker
from anki_miner.models.processing import TerminalOutcome
from anki_miner.services.mokuro_runner import MokuroOptions, MokuroResult, MokuroStatus
from anki_miner.services.mokuro_volumes import MokuroVolume


def _vol(tmp_path: Path, name: str, *, processed: bool = False) -> MokuroVolume:
    src = tmp_path / name
    src.mkdir(exist_ok=True)
    out = tmp_path / f"{name}.mokuro"
    if processed:
        out.write_text("{}")
    return MokuroVolume(src, out, processed)


def _worker(tmp_path, volumes, service, **kw) -> MokuroWorker:
    config = AnkiMinerConfig(media_temp_folder=tmp_path / "tmp", uv_root=tmp_path / "uv")
    return MokuroWorker(config, volumes, options=MokuroOptions(), service=service, **kw)


class _Recorder:  # identical to test_download_worker._Recorder
    def __init__(self, worker) -> None:
        self.events: list[tuple[str, tuple[Any, ...]]] = []
        for name in ("file_started", "file_progress", "file_finished", "file_skipped", "queue_finished", "error"):
            getattr(worker, name).connect(lambda *a, _n=name: self.events.append((_n, a)))

    def of(self, kind: str):
        return [args for name, args in self.events if name == kind]

    @property
    def outcome(self):
        return self.of("queue_finished")[0][0]


def test_happy_path(tmp_path):
    v1, v2 = _vol(tmp_path, "a"), _vol(tmp_path, "b")
    service = MagicMock()
    service.process_volume.side_effect = lambda vol, *a, **k: MokuroResult(MokuroStatus.DONE, vol.output)
    worker = _worker(tmp_path, [v1, v2], service)
    rec = _Recorder(worker)
    worker.run()
    assert [a[0] for a in rec.of("file_started")] == [0, 1]
    assert rec.of("file_finished") == [(0, v1.output, None), (1, v2.output, None)]
    assert rec.outcome is TerminalOutcome.SUCCESS


def test_existing_sidecar_is_skipped_without_calling_mokuro(tmp_path):
    v = _vol(tmp_path, "a", processed=True)
    service = MagicMock()
    worker = _worker(tmp_path, [v], service)
    rec = _Recorder(worker)
    worker.run()
    service.process_volume.assert_not_called()
    skipped = rec.of("file_skipped")
    assert skipped[0][0] == 0 and skipped[0][1] == v.output and "Redo" in skipped[0][2]
    assert rec.outcome is TerminalOutcome.SUCCESS


def test_redo_processes_existing_sidecar(tmp_path):
    v = _vol(tmp_path, "a", processed=True)
    service = MagicMock()
    service.process_volume.return_value = MokuroResult(MokuroStatus.DONE, v.output)
    worker = _worker(tmp_path, [v], service, skip_processed=False)
    rec = _Recorder(worker)
    worker.run()
    service.process_volume.assert_called_once()
    assert rec.of("file_skipped") == []


def test_per_item_isolation(tmp_path):
    v1, v2 = _vol(tmp_path, "a"), _vol(tmp_path, "b")
    service = MagicMock()
    service.process_volume.side_effect = [MokuroError("boom"), MokuroResult(MokuroStatus.DONE, v2.output)]
    worker = _worker(tmp_path, [v1, v2], service)
    rec = _Recorder(worker)
    worker.run()
    finished = rec.of("file_finished")
    assert finished[0][1] is None and "boom" in finished[0][2]
    assert finished[1] == (1, v2.output, None)
    assert rec.outcome is TerminalOutcome.PARTIAL


def test_missing_mokuro_stops_queue_not_cancelled(tmp_path):
    vols = [_vol(tmp_path, n) for n in "abc"]
    service = MagicMock()
    service.process_volume.side_effect = MokuroNotFoundError("no mokuro")
    worker = _worker(tmp_path, vols, service)
    rec = _Recorder(worker)
    worker.run()
    assert service.process_volume.call_count == 1
    assert len(rec.of("file_finished")) == 1
    assert worker.is_cancelled is False
    assert rec.outcome is TerminalOutcome.FAILED


def test_cancel_mid_item(tmp_path):
    vols = [_vol(tmp_path, n) for n in "ab"]
    service = MagicMock()

    def _cancelling(*_a, **_k):
        worker.cancel()
        return MokuroResult(MokuroStatus.CANCELLED, None)

    service.process_volume.side_effect = _cancelling
    worker = _worker(tmp_path, vols, service)
    rec = _Recorder(worker)
    worker.run()
    assert service.process_volume.call_count == 1
    assert rec.outcome is TerminalOutcome.CANCELLED


def test_progress_mapping_and_cancel_event(tmp_path):
    v = _vol(tmp_path, "a")
    service = MagicMock()

    def _run(vol, options, **kwargs):
        kwargs["progress_cb"]("Page 3 of 25", 0.12)
        kwargs["progress_cb"]("Loading models (cpu)", None)
        return MokuroResult(MokuroStatus.DONE, vol.output)

    service.process_volume.side_effect = _run
    worker = _worker(tmp_path, [v], service)
    rec = _Recorder(worker)
    worker.run()
    assert (0, 12, "Page 3 of 25") in rec.of("file_progress")
    assert (0, 0, "Loading models (cpu)") in rec.of("file_progress")
    assert service.process_volume.call_args.kwargs["cancel_event"] is worker._cancel_event
