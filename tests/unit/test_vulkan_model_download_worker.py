"""Tests for the Vulkan model install task run through InstallWorker.

Post-ARC-010 the per-resource ``VulkanModelDownloadWorker`` collapsed into
``InstallWorker`` + ``vulkan_model_task``; one run fetches BOTH the ggml
acoustic model and the Silero VAD via the ggml_model_installer seam, so both
``install_ggml_model`` and ``install_vad_model`` are mocked here.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtCore")

from anki_miner.exceptions import SetupError
from anki_miner.gui.workers.install_worker import InstallWorker, vulkan_model_task
from tests.unit._worker_sync import _run_worker_sync

_MOD = "anki_miner.services.asr.ggml_model_installer"


def _worker(asr_model: str, asr_models_root) -> InstallWorker:
    return InstallWorker(vulkan_model_task(asr_model, asr_models_root))


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
    worker = _worker("large-v3", tmp_path)

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
    worker = _worker("large-v3", tmp_path)
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
    worker = _worker("small", tmp_path)

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
    worker = _worker("large-v3", tmp_path)
    worker.status.connect(statuses.append)

    _run_worker_sync(worker)

    # The starting status plus at least one progress-derived status line.
    assert len(statuses) >= 2
    assert any("%" in s for s in statuses)


def test_progress_resolves_under_vulkan_context(qapp, tmp_path, monkeypatch):
    """The ``%1 (%2%)`` template resolves under the Vulkan context, NOT CudaPack.

    Regression guard: fr/zh_cn carry a distinct Vulkan variant (non-breaking
    space / fullwidth parens), so a progress line during a Vulkan download must
    resolve under ``VulkanModelDownloadWorker``.
    """
    import anki_miner.gui.workers.install_worker as iw

    seen_ctx: list[str] = []
    monkeypatch.setattr(iw, "_progress_template", lambda ctx: seen_ctx.append(ctx) or "%1 (%2%)")

    def _ggml(asr_model, root, progress=None, cancel_event=None):
        if progress is not None:
            progress(50, 100, "ggml-large-v3")
        return root

    _patch_installers(monkeypatch, ggml=_ggml)
    worker = _worker("large-v3", tmp_path)

    _run_worker_sync(worker)

    assert seen_ctx == ["VulkanModelDownloadWorker"]
    assert worker._progress_ctx == "VulkanModelDownloadWorker"


def test_progress_template_routes_each_context_to_its_own(qapp, monkeypatch):
    """``_progress_template`` resolves the source under the passed-in context."""
    from PyQt6.QtCore import QCoreApplication

    import anki_miner.gui.workers.install_worker as iw

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(QCoreApplication, "translate", lambda ctx, src: calls.append((ctx, src)) or src)

    iw._progress_template("VulkanModelDownloadWorker")
    iw._progress_template("OnnxPackDownloadWorker")
    iw._progress_template("CudaPackDownloadWorker")
    iw._progress_template("SomethingElse")  # falls back to CudaPack

    assert calls == [
        ("VulkanModelDownloadWorker", "%1 (%2%)"),
        ("OnnxPackDownloadWorker", "%1 (%2%)"),
        ("CudaPackDownloadWorker", "%1 (%2%)"),
        ("CudaPackDownloadWorker", "%1 (%2%)"),
    ]


def test_failure_emits_result_false(qapp, tmp_path, monkeypatch):
    def _ggml(asr_model, root, progress=None, cancel_event=None):
        raise SetupError("checksum mismatch")

    _patch_installers(monkeypatch, ggml=_ggml)
    worker = _worker("large-v3", tmp_path)

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
    worker = _worker("large-v3", tmp_path)

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
    worker = _worker("large-v3", tmp_path)
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
    worker = _worker("large-v3", tmp_path)
    _run_worker_sync(worker)

    assert len(received) == 2
    assert all(ev is worker.cancel_event for ev in received)


def test_cancel_during_install_suppresses_result(qapp, tmp_path, monkeypatch):
    """A failure raised after cancel() emits no result_ready."""

    def _ggml(asr_model, root, progress=None, cancel_event=None):
        raise SetupError("installation cancelled")

    _patch_installers(monkeypatch, ggml=_ggml)
    worker = _worker("large-v3", tmp_path)
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
    worker = _worker("large-v3", tmp_path)

    results: list[tuple] = []
    worker.result_ready.connect(lambda ok, msg: results.append((ok, msg)))

    _run_worker_sync(worker)

    assert vad_calls == []
    assert results == []


def test_runs_on_thread_and_joins(qapp, qtbot, tmp_path, monkeypatch):
    """The worker runs to completion on its QThread and is joinable with wait()."""
    _patch_installers(monkeypatch)
    worker = _worker("large-v3", tmp_path)

    results: list[tuple] = []
    worker.result_ready.connect(lambda ok, msg: results.append((ok, msg)))

    worker.start()
    assert worker.wait(5000)
    qtbot.waitUntil(lambda: len(results) == 1, timeout=5000)
    assert results[0][0] is True
