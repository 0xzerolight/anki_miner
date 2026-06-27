"""Tests for OnnxPackDownloadWorker — success/failure/cancel with mocked install."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtCore")

from anki_miner.gui.workers.onnx_pack_download_worker import OnnxPackDownloadWorker

_INSTALL = "anki_miner.gui.workers.onnx_pack_download_worker.install_onnx_pack"


def _run_worker_sync(worker: OnnxPackDownloadWorker) -> None:
    """Run the worker's run() synchronously (bypass QThread.start)."""
    worker.run()


def test_success_emits_result_true(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(_INSTALL, lambda onnx_pack_root, progress=None, cancel_event=None: onnx_pack_root)
    worker = OnnxPackDownloadWorker(tmp_path)

    results: list[tuple] = []
    worker.result_ready.connect(lambda ok, msg: results.append((ok, msg)))

    _run_worker_sync(worker)

    assert len(results) == 1
    ok, msg = results[0]
    assert ok is True
    assert isinstance(msg, str)


def test_success_emits_status_before_result(qapp, tmp_path, monkeypatch):
    statuses: list[str] = []
    monkeypatch.setattr(_INSTALL, lambda onnx_pack_root, progress=None, cancel_event=None: onnx_pack_root)
    worker = OnnxPackDownloadWorker(tmp_path)
    worker.status.connect(statuses.append)

    results: list[tuple] = []
    worker.result_ready.connect(lambda ok, msg: results.append((ok, msg)))

    _run_worker_sync(worker)

    assert len(statuses) >= 1
    assert len(results) == 1


def test_progress_adapter_emits_status(qapp, tmp_path, monkeypatch):
    statuses: list[str] = []

    def _install(onnx_pack_root, progress=None, cancel_event=None):
        if progress is not None:
            progress(50, 100, "onnxruntime: downloading")
        return onnx_pack_root

    monkeypatch.setattr(_INSTALL, _install)
    worker = OnnxPackDownloadWorker(tmp_path)
    worker.status.connect(statuses.append)

    _run_worker_sync(worker)

    assert len(statuses) >= 2
    assert any("%" in s for s in statuses)


def test_failure_emits_result_false(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(
        _INSTALL,
        lambda onnx_pack_root, progress=None, cancel_event=None: (_ for _ in ()).throw(
            RuntimeError("checksum mismatch")
        ),
    )
    worker = OnnxPackDownloadWorker(tmp_path)

    results: list[tuple] = []
    worker.result_ready.connect(lambda ok, msg: results.append((ok, msg)))

    _run_worker_sync(worker)

    assert len(results) == 1
    ok, msg = results[0]
    assert ok is False
    assert "checksum mismatch" in msg


def test_cancel_before_run_skips_install(qapp, tmp_path, monkeypatch):
    calls: list[object] = []
    monkeypatch.setattr(
        _INSTALL,
        lambda onnx_pack_root, progress=None, cancel_event=None: calls.append(onnx_pack_root),
    )
    worker = OnnxPackDownloadWorker(tmp_path)
    worker.cancel()

    results: list[tuple] = []
    worker.result_ready.connect(lambda ok, msg: results.append((ok, msg)))

    _run_worker_sync(worker)

    assert calls == []
    assert results == []


def test_cancel_event_passed_to_install(qapp, tmp_path, monkeypatch):
    received: list[object] = []
    monkeypatch.setattr(
        _INSTALL,
        lambda onnx_pack_root, progress=None, cancel_event=None: received.append(cancel_event) or onnx_pack_root,
    )
    worker = OnnxPackDownloadWorker(tmp_path)
    _run_worker_sync(worker)

    assert len(received) == 1
    assert received[0] is worker._cancel_event


def test_cancel_during_install_suppresses_result(qapp, tmp_path, monkeypatch):
    """A failure raised after cancel() emits no result_ready."""

    def _install(onnx_pack_root, progress=None, cancel_event=None):
        raise RuntimeError("installation cancelled")

    monkeypatch.setattr(_INSTALL, _install)
    worker = OnnxPackDownloadWorker(tmp_path)
    worker.cancel()

    results: list[tuple] = []
    worker.result_ready.connect(lambda ok, msg: results.append((ok, msg)))

    _run_worker_sync(worker)

    assert results == []
