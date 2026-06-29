"""Tests for VulkanModelDownloadWorker — success/failure/cancel with mocked install.

Mirrors ``test_cuda_pack_download_worker``: the worker fetches BOTH the ggml
acoustic model and the Silero VAD via the ggml_model_installer seam, so both
``install_ggml_model`` and ``install_vad_model`` are mocked here.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtCore")

from anki_miner.exceptions import SetupError
from anki_miner.gui.workers.vulkan_model_download_worker import VulkanModelDownloadWorker

_MOD = "anki_miner.gui.workers.vulkan_model_download_worker"


def _run_worker_sync(worker: VulkanModelDownloadWorker) -> None:
    """Run the worker's run() synchronously (bypass QThread.start)."""
    worker.run()


def _patch_installers(monkeypatch, *, ggml=None, vad=None) -> None:
    monkeypatch.setattr(
        f"{_MOD}.install_ggml_model",
        ggml or (lambda asr_model, root, progress=None, cancel_event=None: root),
    )
    monkeypatch.setattr(
        f"{_MOD}.install_vad_model",
        vad or (lambda root, progress=None, cancel_event=None: root),
    )


def test_success_emits_result_true(qapp, tmp_path, monkeypatch):
    _patch_installers(monkeypatch)
    worker = VulkanModelDownloadWorker("large-v3", tmp_path)

    results: list[tuple] = []
    worker.result_ready.connect(lambda ok, msg: results.append((ok, msg)))

    _run_worker_sync(worker)

    assert len(results) == 1
    ok, msg = results[0]
    assert ok is True
    assert isinstance(msg, str)


def test_success_emits_status_before_result(qapp, tmp_path, monkeypatch):
    _patch_installers(monkeypatch)
    statuses: list[str] = []
    worker = VulkanModelDownloadWorker("large-v3", tmp_path)
    worker.status.connect(statuses.append)

    results: list[tuple] = []
    worker.result_ready.connect(lambda ok, msg: results.append((ok, msg)))

    _run_worker_sync(worker)

    assert len(statuses) >= 1
    assert len(results) == 1


def test_installs_both_acoustic_and_vad(qapp, tmp_path, monkeypatch):
    """Both the acoustic model AND the VAD are installed in one run."""
    ggml_calls: list[tuple] = []
    vad_calls: list = []

    def _ggml(asr_model, root, progress=None, cancel_event=None):
        ggml_calls.append((asr_model, root))
        return root

    def _vad(root, progress=None, cancel_event=None):
        vad_calls.append(root)
        return root

    _patch_installers(monkeypatch, ggml=_ggml, vad=_vad)
    worker = VulkanModelDownloadWorker("small", tmp_path)

    _run_worker_sync(worker)

    assert ggml_calls == [("small", tmp_path)]
    assert vad_calls == [tmp_path]


def test_progress_adapter_emits_status(qapp, tmp_path, monkeypatch):
    statuses: list[str] = []

    def _ggml(asr_model, root, progress=None, cancel_event=None):
        if progress is not None:
            progress(50, 100, "ggml-large-v3")
        return root

    _patch_installers(monkeypatch, ggml=_ggml)
    worker = VulkanModelDownloadWorker("large-v3", tmp_path)
    worker.status.connect(statuses.append)

    _run_worker_sync(worker)

    # The starting status plus at least one progress-derived status line.
    assert len(statuses) >= 2
    assert any("%" in s for s in statuses)


def test_failure_emits_result_false(qapp, tmp_path, monkeypatch):
    def _ggml(asr_model, root, progress=None, cancel_event=None):
        raise SetupError("checksum mismatch")

    _patch_installers(monkeypatch, ggml=_ggml)
    worker = VulkanModelDownloadWorker("large-v3", tmp_path)

    results: list[tuple] = []
    worker.result_ready.connect(lambda ok, msg: results.append((ok, msg)))

    _run_worker_sync(worker)

    assert len(results) == 1
    ok, msg = results[0]
    assert ok is False
    assert "checksum mismatch" in msg


def test_vad_failure_emits_result_false(qapp, tmp_path, monkeypatch):
    """A SetupError raised by the VAD install also surfaces as a failure."""

    def _vad(root, progress=None, cancel_event=None):
        raise SetupError("vad download failed")

    _patch_installers(monkeypatch, vad=_vad)
    worker = VulkanModelDownloadWorker("large-v3", tmp_path)

    results: list[tuple] = []
    worker.result_ready.connect(lambda ok, msg: results.append((ok, msg)))

    _run_worker_sync(worker)

    assert len(results) == 1
    ok, msg = results[0]
    assert ok is False
    assert "vad download failed" in msg


def test_cancel_before_run_skips_install(qapp, tmp_path, monkeypatch):
    calls: list[object] = []
    _patch_installers(
        monkeypatch,
        ggml=lambda asr_model, root, progress=None, cancel_event=None: calls.append(root),
        vad=lambda root, progress=None, cancel_event=None: calls.append(root),
    )
    worker = VulkanModelDownloadWorker("large-v3", tmp_path)
    worker.cancel()

    results: list[tuple] = []
    worker.result_ready.connect(lambda ok, msg: results.append((ok, msg)))

    _run_worker_sync(worker)

    assert calls == []
    assert results == []


def test_cancel_event_passed_to_install(qapp, tmp_path, monkeypatch):
    received: list[object] = []
    _patch_installers(
        monkeypatch,
        ggml=lambda asr_model, root, progress=None, cancel_event=None: received.append(cancel_event) or root,
        vad=lambda root, progress=None, cancel_event=None: received.append(cancel_event) or root,
    )
    worker = VulkanModelDownloadWorker("large-v3", tmp_path)
    _run_worker_sync(worker)

    assert len(received) == 2
    assert all(ev is worker._cancel_event for ev in received)


def test_cancel_during_install_suppresses_result(qapp, tmp_path, monkeypatch):
    """A failure raised after cancel() emits no result_ready."""

    def _ggml(asr_model, root, progress=None, cancel_event=None):
        raise SetupError("installation cancelled")

    _patch_installers(monkeypatch, ggml=_ggml)
    worker = VulkanModelDownloadWorker("large-v3", tmp_path)
    worker.cancel()

    results: list[tuple] = []
    worker.result_ready.connect(lambda ok, msg: results.append((ok, msg)))

    _run_worker_sync(worker)

    assert results == []


def test_cancel_between_installs_skips_vad(qapp, tmp_path, monkeypatch):
    """Cancelling during the acoustic install means the VAD install never runs."""
    vad_calls: list = []

    def _ggml(asr_model, root, progress=None, cancel_event=None):
        worker.cancel()  # noqa: F821 — bound below before run()
        return root

    _patch_installers(
        monkeypatch,
        ggml=_ggml,
        vad=lambda root, progress=None, cancel_event=None: vad_calls.append(root),
    )
    worker = VulkanModelDownloadWorker("large-v3", tmp_path)

    results: list[tuple] = []
    worker.result_ready.connect(lambda ok, msg: results.append((ok, msg)))

    _run_worker_sync(worker)

    assert vad_calls == []
    assert results == []


def test_runs_on_thread_and_joins(qapp, qtbot, tmp_path, monkeypatch):
    """The worker runs to completion on its QThread and is joinable with wait()."""
    _patch_installers(monkeypatch)
    worker = VulkanModelDownloadWorker("large-v3", tmp_path)

    results: list[tuple] = []
    worker.result_ready.connect(lambda ok, msg: results.append((ok, msg)))

    worker.start()
    assert worker.wait(5000)
    qtbot.waitUntil(lambda: len(results) == 1, timeout=5000)
    assert results[0][0] is True
