"""Tests for the mokuro install task run through InstallWorker.

Mirrors the shared generic behaviours in ``tests/unit/test_install_tasks.py``: the per-resource
worker is ``InstallWorker`` + a task builder, so these construct that pairing and exercise
success/failure/cancel-forwarding with a mocked ``install_mokuro``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtCore")

from PyQt6.QtCore import QTranslator

from anki_miner.gui.workers.install_worker import InstallWorker, mokuro_install_task
from anki_miner.services.ocr.recognizers import RECOGNIZERS
from tests.unit._worker_sync import _run_worker_sync

_INSTALL = "anki_miner.services.mokuro_installer.install_mokuro"
_ADD_ONNXRUNTIME = "anki_miner.services.mokuro_installer.add_onnxruntime"
_ONNXRUNTIME_INSTALLED = "anki_miner.services.mokuro_installer.onnxruntime_installed"
_INSTALL_MODELS = "anki_miner.services.ocr.model_installer.install_models"
_MANAGED = "anki_miner.utils.mokuro_resolver.managed_mokuro_installed"


def _worker(tmp_path) -> InstallWorker:
    return InstallWorker(
        mokuro_install_task(tmp_path / "bin", tmp_path / "uv", tmp_path / "models", None, reinstall=False)
    )


def test_success_emits_result_true_and_forwards_status(qapp, tmp_path, monkeypatch):
    def fake(bin_root, uv_root, *, status=None, progress=None, cancel_event=None):
        assert bin_root == tmp_path / "bin" and uv_root == tmp_path / "uv"
        status("Resolved 46 packages")
        return uv_root / "mokuro" / "bin" / "mokuro"

    monkeypatch.setattr(_INSTALL, fake)
    worker = _worker(tmp_path)
    statuses: list[str] = []
    results: list[tuple] = []
    worker.status.connect(statuses.append)
    worker.result_ready.connect(lambda ok, msg: results.append((ok, msg)))
    _run_worker_sync(worker)
    assert "Resolved 46 packages" in statuses
    assert results == [(True, results[0][1])] and "mokuro" in results[0][1]


def test_failure_emits_result_false(qapp, tmp_path, monkeypatch):
    from anki_miner.exceptions import SetupError

    def boom(*a, **k):
        raise SetupError("uv pip install failed")

    monkeypatch.setattr(_INSTALL, boom)
    worker = _worker(tmp_path)
    results: list[tuple] = []
    worker.result_ready.connect(lambda ok, msg: results.append((ok, msg)))
    _run_worker_sync(worker)
    assert results and results[0][0] is False and "uv pip install failed" in results[0][1]


def test_cancel_event_is_forwarded(qapp, tmp_path, monkeypatch):
    seen = {}

    def fake(bin_root, uv_root, *, status=None, progress=None, cancel_event=None):
        seen["ev"] = cancel_event
        return uv_root

    monkeypatch.setattr(_INSTALL, fake)
    worker = _worker(tmp_path)
    _run_worker_sync(worker)
    assert seen["ev"] is worker.cancel_event


class _PrefixTranslator(QTranslator):
    def translate(
        self,
        context: str | None,
        source_text: str | None,
        disambiguation: str | None = None,
        n: int = -1,
    ) -> str:
        del context, disambiguation, n
        return f"translated:{source_text or ''}"


def test_installer_phase_lines_are_translated_and_uv_lines_pass_through(qapp, tmp_path, monkeypatch):
    from anki_miner.services import mokuro_installer as mi

    def fake(bin_root, uv_root, *, status=None, progress=None, cancel_event=None):
        status(mi.STATUS_DOWNLOADING_UV)
        status(mi.STATUS_PREPARING_PYTHON)
        status(mi.STATUS_INSTALLING_MOKURO)
        status("Resolved 46 packages in 2.31s")
        status(mi.STATUS_DOWNLOADING_PACKAGES)
        return uv_root

    monkeypatch.setattr(_INSTALL, fake)
    translator = _PrefixTranslator()
    qapp.installTranslator(translator)
    try:
        worker = _worker(tmp_path)
        statuses: list[str] = []
        worker.status.connect(statuses.append)
        _run_worker_sync(worker)
    finally:
        qapp.removeTranslator(translator)

    assert statuses == [
        "translated:Installing mokuro…",
        "translated:Downloading uv…",
        f"translated:Preparing Python {mi.MOKURO_PYTHON}…",
        f"translated:Installing {mi.MOKURO_REQUIREMENT}…",
        "Resolved 46 packages in 2.31s",  # raw uv output is not a catalog string
        "translated:Downloading packages — torch is large, this can take a while…",
    ]


class TestWhatTheTaskInstalls:
    """The task fetches only what the mining language is missing: mokuro, then onnxruntime, then the model."""

    @staticmethod
    def _run(tmp_path, monkeypatch, *, managed: bool, onnxruntime: bool, recognizer, reinstall: bool):
        calls: list[tuple] = []
        monkeypatch.setattr(_MANAGED, lambda uv_root: managed)
        monkeypatch.setattr(_ONNXRUNTIME_INSTALLED, lambda uv_root: onnxruntime)
        monkeypatch.setattr(
            _INSTALL, lambda bin_root, uv_root, **kw: calls.append(("install_mokuro", bin_root, uv_root))
        )
        monkeypatch.setattr(
            _ADD_ONNXRUNTIME, lambda bin_root, uv_root, **kw: calls.append(("add_onnxruntime", bin_root, uv_root))
        )
        monkeypatch.setattr(
            _INSTALL_MODELS,
            lambda root, specs=None, *, progress=None, cancel_event=None: calls.append(("install_models", root, specs)),
        )
        task = mokuro_install_task(
            tmp_path / "bin", tmp_path / "uv", tmp_path / "models", recognizer, reinstall=reinstall
        )
        worker = InstallWorker(task)
        statuses: list[str] = []
        results: list[tuple] = []
        worker.status.connect(statuses.append)
        worker.result_ready.connect(lambda ok, msg: results.append((ok, msg)))
        _run_worker_sync(worker)
        assert results and results[0][0] is True, results
        return [call[0] for call in calls], calls, statuses

    def test_a_managed_japanese_venv_switching_language_gets_only_the_runtime_and_the_model(
        self, qapp, tmp_path, monkeypatch
    ):
        """Review Focus 1: never torch again, only onnxruntime and the model."""
        spec = RECOGNIZERS["korean"]
        names, calls, statuses = self._run(
            tmp_path, monkeypatch, managed=True, onnxruntime=False, recognizer=spec, reinstall=False
        )

        assert names == ["add_onnxruntime", "install_models"]
        assert calls[0][1:] == (tmp_path / "bin", tmp_path / "uv")
        assert calls[1][1:] == (tmp_path / "models", (spec.model,))
        assert statuses[:1] == ["Adding the OCR runtime…"]
        assert "Downloading the OCR model…" in statuses

    def test_a_fresh_recogniser_install_runs_mokuro_then_the_runtime_then_the_model(self, qapp, tmp_path, monkeypatch):
        names, _calls, _statuses = self._run(
            tmp_path, monkeypatch, managed=False, onnxruntime=False, recognizer=RECOGNIZERS["korean"], reinstall=False
        )

        assert names == ["install_mokuro", "add_onnxruntime", "install_models"]

    def test_a_reinstall_runs_the_full_install_over_a_managed_mokuro(self, qapp, tmp_path, monkeypatch):
        """A reinstall --clears the venv, so onnxruntime is added back after it."""
        names, _calls, _statuses = self._run(
            tmp_path, monkeypatch, managed=True, onnxruntime=False, recognizer=RECOGNIZERS["korean"], reinstall=True
        )

        assert names == ["install_mokuro", "add_onnxruntime", "install_models"]

    def test_a_venv_that_has_onnxruntime_fetches_only_the_model(self, qapp, tmp_path, monkeypatch):
        names, _calls, _statuses = self._run(
            tmp_path, monkeypatch, managed=True, onnxruntime=True, recognizer=RECOGNIZERS["korean"], reinstall=False
        )

        assert names == ["install_models"]

    @pytest.mark.parametrize(("managed", "reinstall"), [(False, False), (True, True), (False, True)])
    def test_japanese_never_adds_the_runtime_or_a_model(self, qapp, tmp_path, monkeypatch, managed, reinstall):
        names, _calls, _statuses = self._run(
            tmp_path, monkeypatch, managed=managed, onnxruntime=False, recognizer=None, reinstall=reinstall
        )

        assert names == ["install_mokuro"]
