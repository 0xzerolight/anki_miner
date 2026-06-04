"""Tests for PrewarmWorker (best-effort MeCab + dictionary cache warming)."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtCore")

from PyQt6.QtCore import QCoreApplication

from anki_miner.gui.workers.prewarm_worker import PrewarmWorker


@pytest.fixture
def qapp():
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


def test_prewarm_worker_runs_and_finishes(test_config, qapp):
    """Worker warms caches in a real thread and emits ``finished`` exactly once."""
    worker = PrewarmWorker(test_config)
    finished_count: list[int] = []
    worker.finished.connect(lambda: finished_count.append(1))

    worker.start()
    assert worker.wait(30_000), "PrewarmWorker did not finish within 30s"
    # Pump the event loop so the queued finished slot fires.
    qapp.processEvents()

    assert sum(finished_count) == 1


def test_prewarm_swallows_tagger_failure(test_config, qapp, monkeypatch):
    """A failure inside run() is swallowed and ``finished`` still emits."""
    import anki_miner.gui.workers.prewarm_worker as mod

    def boom(*_args, **_kwargs):
        raise RuntimeError("simulated MeCab dict missing")

    monkeypatch.setattr(mod.fugashi, "Tagger", boom)

    worker = PrewarmWorker(test_config)
    finished_count: list[int] = []
    worker.finished.connect(lambda: finished_count.append(1))

    worker.start()
    assert worker.wait(30_000), "PrewarmWorker did not finish within 30s"
    qapp.processEvents()
    assert sum(finished_count) == 1


def test_prewarm_swallows_registry_failure(test_config, qapp, monkeypatch):
    """A failure warming the dictionary chain is swallowed; ``finished`` emits."""
    import anki_miner.gui.workers.prewarm_worker as mod

    def boom(*_args, **_kwargs):
        raise RuntimeError("simulated sqlite open failure")

    monkeypatch.setattr(mod.DictionaryRegistry, "load", boom)

    worker = PrewarmWorker(test_config)
    finished_count: list[int] = []
    worker.finished.connect(lambda: finished_count.append(1))

    worker.start()
    assert worker.wait(30_000), "PrewarmWorker did not finish within 30s"
    qapp.processEvents()
    assert sum(finished_count) == 1


def test_prewarm_does_not_expose_shared_state(test_config, qapp):
    """The worker must not retain or expose the warmed Tagger / connections."""
    worker = PrewarmWorker(test_config)
    worker.start()
    assert worker.wait(30_000)
    qapp.processEvents()

    # No leaked tagger/registry/connection attributes on the worker instance.
    assert not hasattr(worker, "tagger")
    assert not hasattr(worker, "registry")
    assert not hasattr(worker, "_tagger")
    assert not hasattr(worker, "_registry")
