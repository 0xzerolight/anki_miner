"""Tests for AlassInstallWorker — success/failure/cancel paths with mocked install."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtCore")

from anki_miner.gui.workers.alass_install_worker import AlassInstallWorker


def _run_worker_sync(worker: AlassInstallWorker) -> None:
    """Run the worker's run() synchronously (bypass QThread.start)."""
    worker.run()


def test_success_emits_result_true(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "anki_miner.gui.workers.alass_install_worker.install_alass",
        lambda bin_root, cancel_event=None: bin_root / "alass",
    )
    worker = AlassInstallWorker(tmp_path)

    results: list[tuple] = []
    worker.result_ready.connect(lambda ok, msg: results.append((ok, msg)))

    _run_worker_sync(worker)

    assert len(results) == 1
    ok, msg = results[0]
    assert ok is True
    assert isinstance(msg, str)


def test_success_emits_status_before_result(qapp, tmp_path, monkeypatch):
    statuses: list[str] = []
    monkeypatch.setattr(
        "anki_miner.gui.workers.alass_install_worker.install_alass",
        lambda bin_root, cancel_event=None: bin_root / "alass",
    )
    worker = AlassInstallWorker(tmp_path)
    worker.status.connect(statuses.append)

    results: list[tuple] = []
    worker.result_ready.connect(lambda ok, msg: results.append((ok, msg)))

    _run_worker_sync(worker)

    assert len(statuses) >= 1
    assert len(results) == 1


def test_failure_emits_result_false(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "anki_miner.gui.workers.alass_install_worker.install_alass",
        lambda bin_root, cancel_event=None: (_ for _ in ()).throw(RuntimeError("checksum mismatch")),
    )
    worker = AlassInstallWorker(tmp_path)

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
        "anki_miner.gui.workers.alass_install_worker.install_alass",
        lambda bin_root, cancel_event=None: calls.append(bin_root),
    )
    worker = AlassInstallWorker(tmp_path)
    worker.cancel()

    results: list[tuple] = []
    worker.result_ready.connect(lambda ok, msg: results.append((ok, msg)))

    _run_worker_sync(worker)

    assert calls == []
    assert results == []


def test_cancel_event_passed_to_install(qapp, tmp_path, monkeypatch):
    received: list[object] = []
    monkeypatch.setattr(
        "anki_miner.gui.workers.alass_install_worker.install_alass",
        lambda bin_root, cancel_event=None: received.append(cancel_event) or (bin_root / "alass"),
    )
    worker = AlassInstallWorker(tmp_path)
    _run_worker_sync(worker)

    assert len(received) == 1
    assert received[0] is worker._cancel_event


def test_cancel_during_install_suppresses_result(qapp, tmp_path, monkeypatch):
    """A failure raised after cancel() emits no result_ready."""

    def _install(bin_root, cancel_event=None):
        raise RuntimeError("download cancelled")

    monkeypatch.setattr("anki_miner.gui.workers.alass_install_worker.install_alass", _install)
    worker = AlassInstallWorker(tmp_path)
    worker.cancel()

    results: list[tuple] = []
    worker.result_ready.connect(lambda ok, msg: results.append((ok, msg)))

    _run_worker_sync(worker)

    assert results == []
