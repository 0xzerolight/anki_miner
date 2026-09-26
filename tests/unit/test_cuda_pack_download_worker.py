"""Tests for the CUDA pack install task's own behaviour (not the shared InstallWorker skeleton).

The six generic success/failure/cancel behaviours live in
``tests/unit/test_install_tasks.py`` (tests-08); this file keeps only what is specific to the
CUDA task: that its progress adapter emits a status line, and that its ``%1 (%2%)`` progress
template resolves under the ``CudaPackDownloadWorker`` context (not another task's).
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtCore")

from anki_miner.gui.workers.install_worker import InstallWorker, cuda_pack_task
from tests.unit._worker_sync import _run_worker_sync

_INSTALL = "anki_miner.services.asr.cuda_pack_installer.install_cuda_pack"


def _worker(cuda_libs_root) -> InstallWorker:
    return InstallWorker(cuda_pack_task(cuda_libs_root))


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
