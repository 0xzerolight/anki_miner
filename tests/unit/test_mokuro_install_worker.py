"""Tests for the mokuro install task run through InstallWorker.

Mirrors ``test_alass_install_worker``: the per-resource worker is
``InstallWorker`` + a task builder, so these construct that pairing and
exercise success/failure/cancel-forwarding with a mocked ``install_mokuro``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtCore")

from PyQt6.QtCore import QTranslator

from anki_miner.gui.workers.install_worker import InstallWorker, mokuro_install_task
from tests.unit._worker_sync import _run_worker_sync

_INSTALL = "anki_miner.services.mokuro_installer.install_mokuro"


def _worker(tmp_path) -> InstallWorker:
    return InstallWorker(mokuro_install_task(tmp_path / "bin", tmp_path / "uv"))


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
