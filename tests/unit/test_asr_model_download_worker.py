"""Tests for AsrModelDownloadWorker — success/failure paths with mocked download fn."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtCore")

from anki_miner.gui.workers.asr_model_download_worker import AsrModelDownloadWorker

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_worker_sync(worker: AsrModelDownloadWorker) -> None:
    """Run the worker's run() synchronously (bypass QThread.start)."""
    worker.run()


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


def test_success_emits_finished_true(qapp, tmp_path, monkeypatch):
    """A successful download emits finished(True, msg)."""
    monkeypatch.setattr(
        "anki_miner.gui.workers.asr_model_download_worker.model_manager.download",
        lambda name, models_root, cancel_event=None: None,
    )

    worker = AsrModelDownloadWorker("large-v3", tmp_path)

    finished: list[tuple] = []
    worker.result_ready.connect(lambda ok, msg: finished.append((ok, msg)))

    _run_worker_sync(worker)

    assert len(finished) == 1
    ok, msg = finished[0]
    assert ok is True
    assert isinstance(msg, str)


def test_success_emits_status_before_finished(qapp, tmp_path, monkeypatch):
    """Worker emits at least one status update before finished."""
    statuses: list[str] = []
    monkeypatch.setattr(
        "anki_miner.gui.workers.asr_model_download_worker.model_manager.download",
        lambda name, models_root, cancel_event=None: None,
    )

    worker = AsrModelDownloadWorker("large-v3", tmp_path)
    worker.status.connect(statuses.append)

    finished: list[tuple] = []
    worker.result_ready.connect(lambda ok, msg: finished.append((ok, msg)))

    _run_worker_sync(worker)

    assert len(statuses) >= 1
    assert len(finished) == 1


# ---------------------------------------------------------------------------
# Failure path
# ---------------------------------------------------------------------------


def test_failure_emits_finished_false(qapp, tmp_path, monkeypatch):
    """A download that raises emits finished(False, err_msg)."""
    monkeypatch.setattr(
        "anki_miner.gui.workers.asr_model_download_worker.model_manager.download",
        lambda name, models_root, cancel_event=None: (_ for _ in ()).throw(RuntimeError("network error")),
    )

    worker = AsrModelDownloadWorker("large-v3", tmp_path)

    finished: list[tuple] = []
    worker.result_ready.connect(lambda ok, msg: finished.append((ok, msg)))

    _run_worker_sync(worker)

    assert len(finished) == 1
    ok, msg = finished[0]
    assert ok is False
    assert "network error" in msg


def test_failure_message_contains_error_text(qapp, tmp_path, monkeypatch):
    """The failure message includes the exception text."""
    monkeypatch.setattr(
        "anki_miner.gui.workers.asr_model_download_worker.model_manager.download",
        lambda name, models_root, cancel_event=None: (_ for _ in ()).throw(ValueError("bad model path")),
    )

    worker = AsrModelDownloadWorker("small", tmp_path)

    finished: list[tuple] = []
    worker.result_ready.connect(lambda ok, msg: finished.append((ok, msg)))

    _run_worker_sync(worker)

    assert not finished[0][0]
    assert "bad model path" in finished[0][1]


# ---------------------------------------------------------------------------
# Cancel path
# ---------------------------------------------------------------------------


def test_cancel_before_run_skips_download(qapp, tmp_path, monkeypatch):
    """cancel() set before run() means download is never called."""
    calls: list[str] = []
    monkeypatch.setattr(
        "anki_miner.gui.workers.asr_model_download_worker.model_manager.download",
        lambda name, models_root, cancel_event=None: calls.append(name),
    )

    worker = AsrModelDownloadWorker("large-v3", tmp_path)
    worker.cancel()

    finished: list[tuple] = []
    worker.result_ready.connect(lambda ok, msg: finished.append((ok, msg)))

    _run_worker_sync(worker)

    assert calls == []
    assert finished == []


# ---------------------------------------------------------------------------
# cancel_event is passed through to download
# ---------------------------------------------------------------------------


def test_cancel_event_passed_to_download(qapp, tmp_path, monkeypatch):
    """The worker passes its _cancel_event to model_manager.download."""
    received_events: list[object] = []
    monkeypatch.setattr(
        "anki_miner.gui.workers.asr_model_download_worker.model_manager.download",
        lambda name, models_root, cancel_event=None: received_events.append(cancel_event),
    )

    worker = AsrModelDownloadWorker("large-v3", tmp_path)
    _run_worker_sync(worker)

    assert len(received_events) == 1
    assert received_events[0] is worker._cancel_event


# ---------------------------------------------------------------------------
# Model name is passed correctly
# ---------------------------------------------------------------------------


def test_model_name_passed_to_download(qapp, tmp_path, monkeypatch):
    """The model name argument is forwarded to model_manager.download."""
    names: list[str] = []
    monkeypatch.setattr(
        "anki_miner.gui.workers.asr_model_download_worker.model_manager.download",
        lambda name, models_root, cancel_event=None: names.append(name),
    )

    worker = AsrModelDownloadWorker("small", tmp_path)
    _run_worker_sync(worker)

    assert names == ["small"]
