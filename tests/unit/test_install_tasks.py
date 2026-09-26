"""Generic InstallWorker task behaviours, shared across every install/download task (tests-08).

Post-ARC-010 the alass, ASR-model, CUDA-pack, onnxruntime-pack and Vulkan-model tasks all run
through the same ``InstallWorker`` skeleton (status -> install -> result_ready). Before this
module, each task's own test file repeated six identical generic tests under task-specific
names: success, status-before-result ordering, failure, cancel-before-run, cancel_event
forwarding, and cancel-during-install suppression. This module drives that shared skeleton once,
parametrized per task; the per-task files keep only their task-specific tests (progress-context
resolution, the Vulkan acoustic+VAD pair, the ASR-model failure-text/name-forwarding checks).

The ASR-model download task has no cancel-during-install-suppression case: its own test file
never had one (its install call is a single synchronous call with no progress hook), so none is
added here — this module moves existing coverage, it does not extend it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtCore")

from anki_miner.exceptions import SetupError
from anki_miner.gui.workers.install_worker import (
    InstallWorker,
    alass_install_task,
    asr_download_task,
    cuda_pack_task,
    onnx_pack_task,
    vulkan_model_task,
)
from tests.unit._worker_sync import _run_worker_sync


@dataclass(frozen=True)
class TaskCase:
    """One InstallWorker task's shape, for the six generic behaviour tests."""

    id: str
    build_worker: Callable[[Path], InstallWorker]
    patch_targets: tuple[str, ...]
    error_cls: type[Exception]
    error_message: str
    has_cancel_during_suppress: bool = field(default=True)


TASKS = [
    TaskCase(
        id="cuda",
        build_worker=lambda tmp_path: InstallWorker(cuda_pack_task(tmp_path)),
        patch_targets=("anki_miner.services.asr.cuda_pack_installer.install_cuda_pack",),
        error_cls=RuntimeError,
        error_message="checksum mismatch",
    ),
    TaskCase(
        id="onnx",
        build_worker=lambda tmp_path: InstallWorker(onnx_pack_task(tmp_path)),
        patch_targets=("anki_miner.services.asr.onnx_pack_installer.install_onnx_pack",),
        error_cls=RuntimeError,
        error_message="checksum mismatch",
    ),
    TaskCase(
        id="alass",
        build_worker=lambda tmp_path: InstallWorker(alass_install_task(tmp_path)),
        patch_targets=("anki_miner.services.alass_installer.install_alass",),
        error_cls=RuntimeError,
        error_message="checksum mismatch",
    ),
    TaskCase(
        id="asr_model",
        build_worker=lambda tmp_path: InstallWorker(asr_download_task("large-v3", tmp_path)),
        patch_targets=("anki_miner.services.asr.model_manager.download",),
        error_cls=RuntimeError,
        error_message="network error",
        has_cancel_during_suppress=False,
    ),
    TaskCase(
        id="vulkan",
        build_worker=lambda tmp_path: InstallWorker(vulkan_model_task("large-v3", tmp_path)),
        patch_targets=(
            "anki_miner.services.asr.ggml_model_installer.install_ggml_model",
            "anki_miner.services.asr.ggml_model_installer.install_vad_model",
        ),
        error_cls=SetupError,
        error_message="checksum mismatch",
    ),
]
TASK_IDS = [t.id for t in TASKS]
CANCEL_DURING_TASKS = [t for t in TASKS if t.has_cancel_during_suppress]
CANCEL_DURING_IDS = [t.id for t in CANCEL_DURING_TASKS]


def _patch_success(monkeypatch: pytest.MonkeyPatch, task: TaskCase) -> None:
    for target in task.patch_targets:
        monkeypatch.setattr(target, lambda *a, **kw: None)


def _patch_recorder(monkeypatch: pytest.MonkeyPatch, task: TaskCase, calls: list) -> None:
    def _record(*a, **kw):
        calls.append(kw.get("cancel_event"))

    for target in task.patch_targets:
        monkeypatch.setattr(target, _record)


def _patch_first_raises(monkeypatch: pytest.MonkeyPatch, task: TaskCase) -> None:
    def _raise(*a, **kw):
        raise task.error_cls(task.error_message)

    monkeypatch.setattr(task.patch_targets[0], _raise)
    for target in task.patch_targets[1:]:
        monkeypatch.setattr(target, lambda *a, **kw: None)


@pytest.mark.parametrize("task", TASKS, ids=TASK_IDS)
def test_success_emits_result_true(qapp, tmp_path, monkeypatch, task):
    _patch_success(monkeypatch, task)
    worker = task.build_worker(tmp_path)

    results: list[tuple] = []
    worker.result_ready.connect(lambda ok, msg: results.append((ok, msg)))

    _run_worker_sync(worker)

    assert len(results) == 1
    ok, msg = results[0]
    assert ok is True
    assert isinstance(msg, str)


@pytest.mark.parametrize("task", TASKS, ids=TASK_IDS)
def test_success_emits_status_before_result(qapp, tmp_path, monkeypatch, task):
    _patch_success(monkeypatch, task)
    statuses: list[str] = []
    worker = task.build_worker(tmp_path)
    worker.status.connect(statuses.append)

    results: list[tuple] = []
    worker.result_ready.connect(lambda ok, msg: results.append((ok, msg)))

    _run_worker_sync(worker)

    assert len(statuses) >= 1
    assert len(results) == 1


@pytest.mark.parametrize("task", TASKS, ids=TASK_IDS)
def test_failure_emits_result_false(qapp, tmp_path, monkeypatch, task):
    _patch_first_raises(monkeypatch, task)
    worker = task.build_worker(tmp_path)

    results: list[tuple] = []
    worker.result_ready.connect(lambda ok, msg: results.append((ok, msg)))

    _run_worker_sync(worker)

    assert len(results) == 1
    ok, msg = results[0]
    assert ok is False
    assert task.error_message in msg


@pytest.mark.parametrize("task", TASKS, ids=TASK_IDS)
def test_cancel_before_run_skips_install(qapp, tmp_path, monkeypatch, task):
    calls: list = []
    _patch_recorder(monkeypatch, task, calls)
    worker = task.build_worker(tmp_path)
    worker.cancel()

    results: list[tuple] = []
    worker.result_ready.connect(lambda ok, msg: results.append((ok, msg)))

    _run_worker_sync(worker)

    assert calls == []
    assert results == []


@pytest.mark.parametrize("task", TASKS, ids=TASK_IDS)
def test_cancel_event_passed_to_install(qapp, tmp_path, monkeypatch, task):
    received: list = []
    _patch_recorder(monkeypatch, task, received)
    worker = task.build_worker(tmp_path)
    _run_worker_sync(worker)

    assert len(received) == len(task.patch_targets)
    assert all(ev is worker.cancel_event for ev in received)


@pytest.mark.parametrize("task", CANCEL_DURING_TASKS, ids=CANCEL_DURING_IDS)
def test_cancel_during_install_suppresses_result(qapp, tmp_path, monkeypatch, task):
    """A failure raised after cancel() emits no result_ready."""
    _patch_first_raises(monkeypatch, task)
    worker = task.build_worker(tmp_path)
    worker.cancel()

    results: list[tuple] = []
    worker.result_ready.connect(lambda ok, msg: results.append((ok, msg)))

    _run_worker_sync(worker)

    assert results == []
