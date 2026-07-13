"""Tests for the alass install task run through InstallWorker.

Post-ARC-010 the per-resource ``AlassInstallWorker`` collapsed into
``InstallWorker`` + ``alass_install_task``; these tests construct that pairing
and exercise success/failure/cancel with a mocked ``install_alass``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtCore")

from anki_miner.gui.workers.install_worker import InstallWorker, alass_install_task

_INSTALL = "anki_miner.services.alass_installer.install_alass"


def _worker(bin_root) -> InstallWorker:
    return InstallWorker(alass_install_task(bin_root))


def _run_worker_sync(worker: InstallWorker) -> None:
    """Run the worker's run() synchronously (bypass QThread.start)."""
    worker.run()


def test_success_emits_result_true(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(_INSTALL, lambda bin_root, cancel_event=None: bin_root / "alass")
    worker = _worker(tmp_path)

    results: list[tuple] = []
    worker.result_ready.connect(lambda ok, msg: results.append((ok, msg)))

    _run_worker_sync(worker)

    assert len(results) == 1
    ok, msg = results[0]
    assert ok is True
    assert isinstance(msg, str)


def test_success_emits_status_before_result(qapp, tmp_path, monkeypatch):
    statuses: list[str] = []
    monkeypatch.setattr(_INSTALL, lambda bin_root, cancel_event=None: bin_root / "alass")
    worker = _worker(tmp_path)
    worker.status.connect(statuses.append)

    results: list[tuple] = []
    worker.result_ready.connect(lambda ok, msg: results.append((ok, msg)))

    _run_worker_sync(worker)

    assert len(statuses) >= 1
    assert len(results) == 1


def test_failure_emits_result_false(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(
        _INSTALL,
        lambda bin_root, cancel_event=None: (_ for _ in ()).throw(RuntimeError("checksum mismatch")),
    )
    worker = _worker(tmp_path)

    results: list[tuple] = []
    worker.result_ready.connect(lambda ok, msg: results.append((ok, msg)))

    _run_worker_sync(worker)

    assert len(results) == 1
    ok, msg = results[0]
    assert ok is False
    assert "checksum mismatch" in msg


def test_cancel_before_run_skips_install(qapp, tmp_path, monkeypatch):
    calls: list[object] = []
    monkeypatch.setattr(_INSTALL, lambda bin_root, cancel_event=None: calls.append(bin_root))
    worker = _worker(tmp_path)
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
        lambda bin_root, cancel_event=None: received.append(cancel_event) or (bin_root / "alass"),
    )
    worker = _worker(tmp_path)
    _run_worker_sync(worker)

    assert len(received) == 1
    assert received[0] is worker.cancel_event


def test_cancel_during_install_suppresses_result(qapp, tmp_path, monkeypatch):
    """A failure raised after cancel() emits no result_ready."""

    def _install(bin_root, cancel_event=None):
        raise RuntimeError("download cancelled")

    monkeypatch.setattr(_INSTALL, _install)
    worker = _worker(tmp_path)
    worker.cancel()

    results: list[tuple] = []
    worker.result_ready.connect(lambda ok, msg: results.append((ok, msg)))

    _run_worker_sync(worker)

    assert results == []
