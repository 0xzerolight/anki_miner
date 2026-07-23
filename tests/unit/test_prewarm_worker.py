"""Tests for PrewarmWorker (best-effort MeCab + dictionary cache warming)."""

from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtCore")

from anki_miner.config import ChainEntry
from anki_miner.gui.utils import service_factory
from anki_miner.gui.workers import prewarm_worker as prewarm_worker_module
from anki_miner.gui.workers.prewarm_worker import PrewarmWorker


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
    import anki_miner.services.tagger as tagger_module

    def boom(*_args, **_kwargs):
        raise RuntimeError("simulated MeCab dict missing")

    monkeypatch.setattr(tagger_module, "get_shared_tagger", boom)

    worker = PrewarmWorker(test_config)
    finished_count: list[int] = []
    worker.finished.connect(lambda: finished_count.append(1))

    worker.start()
    assert worker.wait(30_000), "PrewarmWorker did not finish within 30s"
    qapp.processEvents()
    assert sum(finished_count) == 1


def test_prewarm_swallows_registry_failure(test_config, qapp, monkeypatch):
    """A failure warming the dictionary chain is swallowed; ``finished`` emits."""
    # The registry is now built inside service_factory.build_definition_service;
    # patch it at its definition module so the failure still flows through that
    # shared path into PrewarmWorker.run()'s best-effort swallow.
    from anki_miner.services.dictionary.registry import DictionaryRegistry

    def boom(*_args, **_kwargs):
        raise RuntimeError("simulated sqlite open failure")

    monkeypatch.setattr(DictionaryRegistry, "load", boom)

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


def test_prewarm_closes_definition_service_on_success(test_config, monkeypatch):
    import anki_miner.services.tagger as tagger_module

    definition_service = MagicMock()
    monkeypatch.setattr(tagger_module, "get_shared_tagger", MagicMock())
    monkeypatch.setattr(
        prewarm_worker_module,
        "build_definition_service",
        MagicMock(return_value=definition_service),
    )

    worker = PrewarmWorker(test_config)
    worker.run()

    definition_service.close.assert_called_once_with()


def test_prewarm_closes_definition_service_when_eager_warm_fails(test_config, monkeypatch):
    import anki_miner.services.tagger as tagger_module

    definition_service = MagicMock()
    definition_service.ensure_loaded.side_effect = RuntimeError("warm boom")
    registry = MagicMock()
    registry.build_provider_chain.return_value = []
    config = dataclasses.replace(
        test_config,
        dictionary_chain=(ChainEntry(kind="indexed", dict_id="test-dict", enabled=True),),
    )
    monkeypatch.setattr(tagger_module, "get_shared_tagger", MagicMock())
    monkeypatch.setattr(service_factory, "_load_dict_registry", MagicMock(return_value=registry))
    monkeypatch.setattr(service_factory, "DefinitionService", MagicMock(return_value=definition_service))

    worker = PrewarmWorker(config)
    worker.run()

    definition_service.ensure_loaded.assert_called_once_with()
    definition_service.close.assert_called_once_with()
