"""Tests for the ASR model download task run through InstallWorker.

Post-ARC-010 the per-resource ``AsrModelDownloadWorker`` collapsed into
``InstallWorker`` + ``asr_download_task``; these tests construct that pairing
and exercise success/failure/cancel with a mocked ``model_manager.download``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtCore")

from anki_miner.gui.workers.install_worker import InstallWorker, asr_download_task
from tests.unit._worker_sync import _run_worker_sync

_DOWNLOAD = "anki_miner.services.asr.model_manager.download"


def _worker(name: str, models_root) -> InstallWorker:
    return InstallWorker(asr_download_task(name, models_root))


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


def test_success_emits_finished_true(qapp, tmp_path, monkeypatch):
    """A successful download emits result_ready(True, msg)."""
    monkeypatch.setattr(_DOWNLOAD, lambda name, models_root, cancel_event=None: None)

    worker = _worker("large-v3", tmp_path)

    finished: list[tuple] = []
    worker.result_ready.connect(lambda ok, msg: finished.append((ok, msg)))

    _run_worker_sync(worker)

    assert len(finished) == 1
    ok, msg = finished[0]
    assert ok is True
    assert isinstance(msg, str)


def test_success_emits_status_before_finished(qapp, tmp_path, monkeypatch):
    """Worker emits at least one status update before result_ready."""
    statuses: list[str] = []
    monkeypatch.setattr(_DOWNLOAD, lambda name, models_root, cancel_event=None: None)

    worker = _worker("large-v3", tmp_path)
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
    """A download that raises emits result_ready(False, err_msg)."""
    monkeypatch.setattr(
        _DOWNLOAD,
        lambda name, models_root, cancel_event=None: (_ for _ in ()).throw(RuntimeError("network error")),
    )

    worker = _worker("large-v3", tmp_path)

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
        _DOWNLOAD,
        lambda name, models_root, cancel_event=None: (_ for _ in ()).throw(ValueError("bad model path")),
    )

    worker = _worker("small", tmp_path)

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
    monkeypatch.setattr(_DOWNLOAD, lambda name, models_root, cancel_event=None: calls.append(name))

    worker = _worker("large-v3", tmp_path)
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
    """The worker passes its cancel_event to model_manager.download."""
    received_events: list[object] = []
    monkeypatch.setattr(
        _DOWNLOAD,
        lambda name, models_root, cancel_event=None: received_events.append(cancel_event),
    )

    worker = _worker("large-v3", tmp_path)
    _run_worker_sync(worker)

    assert len(received_events) == 1
    assert received_events[0] is worker.cancel_event


# ---------------------------------------------------------------------------
# Model name is passed correctly
# ---------------------------------------------------------------------------


def test_model_name_passed_to_download(qapp, tmp_path, monkeypatch):
    """The model name argument is forwarded to model_manager.download."""
    names: list[str] = []
    monkeypatch.setattr(_DOWNLOAD, lambda name, models_root, cancel_event=None: names.append(name))

    worker = _worker("small", tmp_path)
    _run_worker_sync(worker)

    assert names == ["small"]
