"""Tests for the CUDA pack install task run through InstallWorker.

Post-ARC-010 the per-resource ``CudaPackDownloadWorker`` collapsed into
``InstallWorker`` + ``cuda_pack_task``; these tests construct that pairing and
exercise success/failure/cancel/progress with a mocked ``install_cuda_pack``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtCore")

from anki_miner.gui.workers.install_worker import InstallWorker, cuda_pack_task

_INSTALL = "anki_miner.services.asr.cuda_pack_installer.install_cuda_pack"


def _worker(cuda_libs_root) -> InstallWorker:
    return InstallWorker(cuda_pack_task(cuda_libs_root))


def _run_worker_sync(worker: InstallWorker) -> None:
    """Run the worker's run() synchronously (bypass QThread.start)."""
    worker.run()


def test_success_emits_result_true(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(_INSTALL, lambda cuda_libs_root, progress=None, cancel_event=None: cuda_libs_root)
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
    monkeypatch.setattr(_INSTALL, lambda cuda_libs_root, progress=None, cancel_event=None: cuda_libs_root)
    worker = _worker(tmp_path)
    worker.status.connect(statuses.append)

    results: list[tuple] = []
    worker.result_ready.connect(lambda ok, msg: results.append((ok, msg)))

    _run_worker_sync(worker)

    assert len(statuses) >= 1
    assert len(results) == 1


def test_progress_adapter_emits_status(qapp, tmp_path, monkeypatch):
    statuses: list[str] = []

    def _install(cuda_libs_root, progress=None, cancel_event=None):
        if progress is not None:
            progress(50, 100, "cudnn")
        return cuda_libs_root

    monkeypatch.setattr(_INSTALL, _install)
    worker = _worker(tmp_path)
    worker.status.connect(statuses.append)

    _run_worker_sync(worker)

    # The starting status plus at least one progress-derived status line.
    assert len(statuses) >= 2
    assert any("%" in s for s in statuses)


def test_progress_resolves_under_cuda_context(qapp, tmp_path, monkeypatch):
    """The ``%1 (%2%)`` template resolves under the CudaPack context (not another)."""
    import anki_miner.gui.workers.install_worker as iw

    seen_ctx: list[str] = []
    monkeypatch.setattr(iw, "_progress_template", lambda ctx: seen_ctx.append(ctx) or "%1 (%2%)")

    def _install(cuda_libs_root, progress=None, cancel_event=None):
        if progress is not None:
            progress(50, 100, "cudnn")
        return cuda_libs_root

    monkeypatch.setattr(_INSTALL, _install)
    worker = _worker(tmp_path)

    _run_worker_sync(worker)

    assert seen_ctx == ["CudaPackDownloadWorker"]
    assert worker._progress_ctx == "CudaPackDownloadWorker"


def test_failure_emits_result_false(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(
        _INSTALL,
        lambda cuda_libs_root, progress=None, cancel_event=None: (_ for _ in ()).throw(
            RuntimeError("checksum mismatch")
        ),
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
    monkeypatch.setattr(
        _INSTALL,
        lambda cuda_libs_root, progress=None, cancel_event=None: calls.append(cuda_libs_root),
    )
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
        lambda cuda_libs_root, progress=None, cancel_event=None: received.append(cancel_event) or cuda_libs_root,
    )
    worker = _worker(tmp_path)
    _run_worker_sync(worker)

    assert len(received) == 1
    assert received[0] is worker.cancel_event


def test_cancel_during_install_suppresses_result(qapp, tmp_path, monkeypatch):
    """A failure raised after cancel() emits no result_ready."""

    def _install(cuda_libs_root, progress=None, cancel_event=None):
        raise RuntimeError("installation cancelled")

    monkeypatch.setattr(_INSTALL, _install)
    worker = _worker(tmp_path)
    worker.cancel()

    results: list[tuple] = []
    worker.result_ready.connect(lambda ok, msg: results.append((ok, msg)))

    _run_worker_sync(worker)

    assert results == []
