"""Tests for gui/workers/deck_filter_worker.py (scan + apply workers)."""

from __future__ import annotations

import dataclasses
import logging
from unittest.mock import MagicMock

from anki_miner.exceptions import AnkiConnectionError
from anki_miner.gui.workers import deck_filter_worker as worker_module
from anki_miner.gui.workers.deck_filter_worker import DeckFilterApplyWorker, DeckFilterScanWorker
from anki_miner.languages.tagger_provider import get_tagger
from anki_miner.services.deck_filter import DeckFilterOptions, DeckFilterPlan, DeckFilterResult
from anki_miner.services.tagger import get_shared_tagger

_OPTIONS = DeckFilterOptions(source_deck="Premade", target_deck="Premade (Filtered)")
_PLAN = DeckFilterPlan(
    options=_OPTIONS,
    kept=(),
    drops=(),
    scanned=0,
    forced_count=0,
    config_version=0,
)
_RESULT = DeckFilterResult(created=3, not_created=1)

_WORKER_MOD = "anki_miner.gui.workers.deck_filter_worker"


def _patch_scan_deps(monkeypatch, *, scan=None, shared_lookup=None):
    shared_lookup = shared_lookup if shared_lookup is not None else MagicMock()
    monkeypatch.setattr(worker_module, "AnkiService", MagicMock())
    monkeypatch.setattr(
        worker_module,
        "create_shared_lookup_services",
        MagicMock(return_value=shared_lookup),
    )
    monkeypatch.setattr(worker_module, "_build_filter_bundle", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(
        worker_module,
        "scan_deck_filter",
        scan if scan is not None else MagicMock(return_value=_PLAN),
    )
    return shared_lookup


class TestDeckFilterScanWorker:
    def test_logs_start_and_completion_summary(self, test_config, monkeypatch, caplog):
        _patch_scan_deps(monkeypatch)

        worker = DeckFilterScanWorker(test_config, _OPTIONS)
        with caplog.at_level(logging.INFO, logger=_WORKER_MOD):
            worker.run()

        start = next(
            record for record in caplog.records if record.getMessage().startswith("DeckFilterScanWorker started:")
        )
        assert start.name == _WORKER_MOD
        done = next(record for record in caplog.records if record.getMessage().startswith("DeckFilterScanWorker done:"))
        assert "kept=0" in done.getMessage()

    def test_emits_plan_with_load_warnings_and_closes_bundle(self, test_config, monkeypatch):
        shared_lookup = MagicMock()
        shared_lookup.load_result.warnings = ["freq source missing"]
        _patch_scan_deps(monkeypatch, shared_lookup=shared_lookup)

        worker = DeckFilterScanWorker(test_config, _OPTIONS)
        received = []
        worker.result_ready.connect(lambda plan, warnings: received.append((plan, warnings)))
        worker.run()

        assert received == [(_PLAN, ("freq source missing",))]
        shared_lookup.close.assert_called_once_with()

    def test_closes_bundle_on_scan_exception_and_emits_error(self, test_config, monkeypatch):
        shared_lookup = MagicMock()
        _patch_scan_deps(
            monkeypatch,
            scan=MagicMock(side_effect=AnkiConnectionError("boom")),
            shared_lookup=shared_lookup,
        )

        worker = DeckFilterScanWorker(test_config, _OPTIONS)
        errors = []
        worker.error.connect(errors.append)
        worker.run()

        assert errors == ["Deck filter scan failed: boom"]
        shared_lookup.close.assert_called_once_with()

    def test_cancel_before_run_emits_nothing(self, test_config, monkeypatch):
        factory = MagicMock()
        monkeypatch.setattr(worker_module, "AnkiService", MagicMock())
        monkeypatch.setattr(worker_module, "create_shared_lookup_services", factory)

        worker = DeckFilterScanWorker(test_config, _OPTIONS)
        worker.cancel()
        received = []
        worker.result_ready.connect(lambda *args: received.append(args))
        worker.run()

        assert received == []
        factory.assert_not_called()


class TestFilterBundleTagger:
    """The scan bundle tokenises with the mining language's own tagger."""

    def test_a_chinese_deck_gets_the_chinese_tagger(self, test_config):
        """The fugashi tagger read 苹果 as りんご/林檎 in the preview's Reading column."""
        bundle = worker_module._build_filter_bundle(dataclasses.replace(test_config, language="zh"), None)

        assert bundle.tagger is get_tagger("zh")
        assert bundle.tagger is not get_shared_tagger()

    def test_a_japanese_deck_keeps_the_shared_tagger(self, test_config):
        """ja resolves through the provider to the same process-wide instance."""
        bundle = worker_module._build_filter_bundle(test_config, None)

        assert bundle.tagger is get_shared_tagger()

    def test_an_uninstalled_engine_degrades_to_no_tagger(self, test_config, monkeypatch):
        """The provider's ValueError leaves readings/lemmas off, never aborts the scan."""
        monkeypatch.setattr(
            "anki_miner.languages.tagger_provider.get_tagger",
            MagicMock(side_effect=ValueError("No tokenizer registered for language: 'zh'")),
        )

        bundle = worker_module._build_filter_bundle(dataclasses.replace(test_config, language="zh"), None)

        assert bundle.tagger is None


class TestFilterBundleWordLists:
    """The scan bundle decodes word lists with the mining language's ladder."""

    def test_a_chinese_blacklist_written_by_notepad_loads(self, test_config, tmp_path):
        """GB18030 is Notepad's default on a mainland machine; UTF-8 dropped the file."""
        bl = tmp_path / "blacklist.txt"
        bl.write_bytes("的\n了\n是\n".encode("gb18030"))
        config = dataclasses.replace(test_config, language="zh", use_blacklist=True, blacklist_path=bl)

        bundle = worker_module._build_filter_bundle(config, None)

        assert bundle.word_list_service is not None
        assert bundle.word_list_service.is_blacklisted("的") is True


class TestDeckFilterApplyWorker:
    def test_emits_result_and_logs_done(self, test_config, monkeypatch, caplog):
        monkeypatch.setattr(worker_module, "AnkiService", MagicMock())
        monkeypatch.setattr(worker_module, "apply_deck_filter", MagicMock(return_value=_RESULT))

        worker = DeckFilterApplyWorker(test_config, _PLAN)
        received = []
        worker.result_ready.connect(received.append)
        with caplog.at_level(logging.INFO, logger=_WORKER_MOD):
            worker.run()

        assert received == [_RESULT]
        done = next(
            record for record in caplog.records if record.getMessage().startswith("DeckFilterApplyWorker done:")
        )
        assert "created=3" in done.getMessage()

    def test_precancelled_emits_empty_receipt_and_cancelled(self, test_config, monkeypatch):
        apply_fn = MagicMock()
        monkeypatch.setattr(worker_module, "AnkiService", MagicMock())
        monkeypatch.setattr(worker_module, "apply_deck_filter", apply_fn)

        worker = DeckFilterApplyWorker(test_config, _PLAN)
        worker.cancel()
        received = []
        cancelled = []
        worker.result_ready.connect(received.append)
        worker.cancelled.connect(lambda: cancelled.append(True))
        worker.run()

        assert received == [DeckFilterResult(0, 0)]
        assert cancelled == [True]
        apply_fn.assert_not_called()

    def test_error_emits_error_signal(self, test_config, monkeypatch):
        monkeypatch.setattr(worker_module, "AnkiService", MagicMock())
        monkeypatch.setattr(
            worker_module,
            "apply_deck_filter",
            MagicMock(side_effect=AnkiConnectionError("deck gone")),
        )

        worker = DeckFilterApplyWorker(test_config, _PLAN)
        errors = []
        worker.error.connect(errors.append)
        worker.run()

        assert errors == ["Deck filter apply failed: deck gone"]
