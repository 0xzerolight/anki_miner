"""Tests for gui/workers/backfill_worker.py (scan + apply workers)."""

from __future__ import annotations

import logging
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from anki_miner.config import PitchSourceEntry
from anki_miner.gui.widgets.backfill_tab import CardBackfillTab
from anki_miner.gui.workers import backfill_worker as backfill_worker_module
from anki_miner.gui.workers.backfill_worker import BackfillApplyWorker, BackfillScanWorker
from anki_miner.services.card_backfiller import BackfillOptions, BackfillPlan, BackfillResult
from anki_miner.services.pitch_accent.source_importer import import_pitch_source

_OPTIONS = BackfillOptions(field_keys=frozenset({"frequency"}))
_PLAN = BackfillPlan(
    options=_OPTIONS,
    notes=(),
    scanned=0,
    skipped_no_identity=0,
    unavailable_fields=(),
    expression_field="Expression",
    config_version=0,
)
_RESULT = BackfillResult(notes_updated=1, fields_filled=2, tagged=1, skipped_stale=0)

_WORKER_MOD = "anki_miner.gui.workers.backfill_worker"


def _lookup_bundle() -> MagicMock:
    """A shared-lookup bundle whose registries report nothing stale.

    A bare ``MagicMock`` answers ``stale_enabled`` with a truthy mock, which the
    scan worker's staleness gate reads as "needs reimport" and aborts on before
    ``scan_backfill`` is ever reached. Tests about anything else start clean.
    """
    bundle = MagicMock()
    for name in ("dictionary_registry", "frequency_registry", "pitch_registry"):
        getattr(bundle, name).stale_enabled.return_value = []
    return bundle


class TestBackfillScanWorker:
    def test_logs_start_and_completion_summary(self, test_config, monkeypatch, caplog):
        shared_lookup = _lookup_bundle()
        monkeypatch.setattr(backfill_worker_module, "AnkiService", MagicMock())
        monkeypatch.setattr(
            backfill_worker_module,
            "create_shared_lookup_services",
            MagicMock(return_value=shared_lookup),
        )
        monkeypatch.setattr(backfill_worker_module, "scan_backfill", MagicMock(return_value=_PLAN))

        worker = BackfillScanWorker(test_config, _OPTIONS)
        with caplog.at_level(logging.INFO, logger=_WORKER_MOD):
            worker.run()

        start = next(
            record for record in caplog.records if record.getMessage().startswith("BackfillScanWorker started:")
        )
        assert start.name == _WORKER_MOD
        done = next(record for record in caplog.records if record.getMessage().startswith("BackfillScanWorker done:"))
        assert "notes=0" in done.getMessage()

    def test_closes_shared_lookup_bundle_on_success(self, test_config, monkeypatch):
        anki_service = MagicMock()
        shared_lookup = _lookup_bundle()
        shared_factory = MagicMock(return_value=shared_lookup)
        scan = MagicMock(return_value=_PLAN)
        monkeypatch.setattr(backfill_worker_module, "AnkiService", MagicMock(return_value=anki_service))
        monkeypatch.setattr(
            backfill_worker_module,
            "create_shared_lookup_services",
            shared_factory,
        )
        monkeypatch.setattr(backfill_worker_module, "scan_backfill", scan)

        worker = BackfillScanWorker(test_config, _OPTIONS)
        worker.run()

        shared_factory.assert_called_once_with(test_config)
        assert scan.call_args.args[2] is shared_lookup
        shared_lookup.close.assert_called_once_with()

    def test_closes_shared_lookup_bundle_on_scan_exception(self, test_config, monkeypatch):
        shared_lookup = _lookup_bundle()
        shared_factory = MagicMock(return_value=shared_lookup)
        monkeypatch.setattr(backfill_worker_module, "AnkiService", MagicMock())
        monkeypatch.setattr(
            backfill_worker_module,
            "create_shared_lookup_services",
            shared_factory,
        )
        monkeypatch.setattr(
            backfill_worker_module,
            "scan_backfill",
            MagicMock(side_effect=RuntimeError("scan boom")),
        )

        worker = BackfillScanWorker(test_config, _OPTIONS)
        errors: list[str] = []
        worker.error.connect(errors.append)
        worker.run()

        assert errors == ["Backfill scan failed: scan boom"]
        shared_lookup.close.assert_called_once_with()

    def test_emits_plan_on_success(self, qtbot, test_config):
        with (
            patch(f"{_WORKER_MOD}.AnkiService") as anki_cls,
            patch(f"{_WORKER_MOD}.create_shared_lookup_services") as factory,
            patch(f"{_WORKER_MOD}.scan_backfill", return_value=_PLAN) as scan,
        ):
            factory.return_value = _lookup_bundle()
            worker = BackfillScanWorker(test_config, _OPTIONS)
            with qtbot.waitSignal(worker.result_ready, timeout=5000) as blocker:
                worker.start()
            worker.wait(5000)
        assert blocker.args == [_PLAN, ()]
        anki_cls.assert_called_once_with(test_config)
        factory.assert_called_once_with(test_config)
        assert scan.call_args[0][0] is anki_cls.return_value
        assert scan.call_args[0][2] is factory.return_value
        factory.return_value.close.assert_called_once_with()

    def test_stale_source_for_a_requested_field_aborts_the_scan(self, test_config, monkeypatch):
        """A frequency backfill against a stale frequency index must not run."""
        shared_lookup = _lookup_bundle()
        shared_lookup.frequency_registry.stale_enabled.return_value = [
            SimpleNamespace(source_id="jpdb", source_name="JPDB")
        ]
        scan = MagicMock(return_value=_PLAN)
        monkeypatch.setattr(backfill_worker_module, "AnkiService", MagicMock())
        monkeypatch.setattr(
            backfill_worker_module,
            "create_shared_lookup_services",
            MagicMock(return_value=shared_lookup),
        )
        monkeypatch.setattr(backfill_worker_module, "scan_backfill", scan)

        worker = BackfillScanWorker(test_config, _OPTIONS)
        errors: list[str] = []
        worker.error.connect(errors.append)
        worker.run()

        assert len(errors) == 1
        assert "JPDB" in errors[0]
        assert "Reimport All" in errors[0]
        scan.assert_not_called()
        shared_lookup.close.assert_called_once_with()

    def test_stale_source_for_an_unrequested_field_does_not_abort(self, test_config, monkeypatch):
        """A frequency-only backfill has no business failing over a stale pitch index."""
        shared_lookup = _lookup_bundle()
        shared_lookup.pitch_registry.stale_enabled.return_value = [SimpleNamespace(source_id="nhk", source_name="NHK")]
        scan = MagicMock(return_value=_PLAN)
        monkeypatch.setattr(backfill_worker_module, "AnkiService", MagicMock())
        monkeypatch.setattr(
            backfill_worker_module,
            "create_shared_lookup_services",
            MagicMock(return_value=shared_lookup),
        )
        monkeypatch.setattr(backfill_worker_module, "scan_backfill", scan)

        worker = BackfillScanWorker(test_config, _OPTIONS)
        errors: list[str] = []
        worker.error.connect(errors.append)
        worker.run()

        assert errors == []
        scan.assert_called_once()

    def test_missing_primary_warning_reaches_mixed_plan_approval(self, qtbot, test_config, tmp_path, monkeypatch):
        pitch_root = tmp_path / "pitch"
        primary_csv = tmp_path / "primary.csv"
        primary_csv.write_text("はし,橋,0\n", encoding="utf-8")
        fallback_csv = tmp_path / "fallback.csv"
        fallback_csv.write_text("はし,橋,1\n", encoding="utf-8")
        import_pitch_source(primary_csv, pitch_root, source_id="primary", source_name="Primary")
        import_pitch_source(fallback_csv, pitch_root, source_id="fallback", source_name="Fallback")
        (pitch_root / "primary").rename(tmp_path / "primary-offline")

        fields = dict(test_config.anki_fields)
        fields.update(
            {
                "expression_reading": "ExpressionReading",
                "expression_furigana": "ExpressionFurigana",
                "pitch_graph": "",
                "pitch_text": "PitchText",
            }
        )
        config = replace(
            test_config,
            anki_fields=fields,
            pitch_root=pitch_root,
            pitch_chain=(PitchSourceEntry("primary"), PitchSourceEntry("fallback")),
        )

        class _BackfillAnki:
            def note_type_names(self):
                return [config.anki_note_type]

            def ordered_note_type_field_names(self, _note_type):
                return ["word", "ExpressionReading", "ExpressionFurigana", "PitchText"]

            def find_notes(self, _query):
                return [1]

            def notes_info(self, _note_ids):
                return [
                    {
                        "noteId": 1,
                        "fields": {
                            "word": {"value": "橋"},
                            "ExpressionReading": {"value": "はし"},
                            "ExpressionFurigana": {"value": ""},
                            "PitchText": {"value": ""},
                        },
                    }
                ]

        monkeypatch.setattr(backfill_worker_module, "AnkiService", lambda _config: _BackfillAnki())
        tab = CardBackfillTab(config)
        qtbot.addWidget(tab)
        tab.field_checkboxes["pitch"].setChecked(True)
        tab.field_checkboxes["reading"].setChecked(True)

        tab._start_scan()
        worker = tab.worker_thread
        assert isinstance(worker, BackfillScanWorker)
        qtbot.waitUntil(lambda: not worker.isRunning(), timeout=5000)
        worker.wait(5000)
        qtbot.waitUntil(lambda: tab.worker_thread is None, timeout=5000)

        warning = "Pitch accent source 'primary' unavailable; skipped"
        assert tab._plan is not None
        planned = {change.field_key for note in tab._plan.notes for change in note.changes}
        assert planned == {"expression_furigana", "pitch_text"}
        assert warning in tab.summary_label.text()
        assert not tab.summary_label.isHidden()
        assert tab.apply_button.isEnabled()

        tab.summary_label.clear()
        tab._set_running(False)
        assert not tab.apply_button.isEnabled()

    def test_emits_error_on_anki_service_valueerror(self, qtbot, test_config):
        with patch(f"{_WORKER_MOD}.AnkiService", side_effect=ValueError("bad mapping")):
            worker = BackfillScanWorker(test_config, _OPTIONS)
            with qtbot.waitSignal(worker.error, timeout=5000) as blocker:
                worker.start()
            worker.wait(5000)
        assert "bad mapping" in blocker.args[0]

    def test_cancellation_suppresses_result(self, qtbot, test_config):
        with (
            patch(f"{_WORKER_MOD}.AnkiService"),
            patch(f"{_WORKER_MOD}.create_shared_lookup_services"),
            patch(f"{_WORKER_MOD}.scan_backfill", return_value=_PLAN),
        ):
            worker = BackfillScanWorker(test_config, _OPTIONS)
            worker.cancel()
            emitted: list[object] = []
            worker.result_ready.connect(emitted.append)
            worker.start()
            worker.wait(5000)
        assert emitted == []

    def test_stale_enabled_dictionary_fails_before_definition_scan(self, test_config, monkeypatch):
        options = BackfillOptions(field_keys=frozenset({"definition"}))
        shared_lookup = MagicMock()
        shared_lookup.dictionary_registry.stale_enabled.return_value = [SimpleNamespace(source_name="Old Dictionary")]
        scan = MagicMock(return_value=_PLAN)
        monkeypatch.setattr(backfill_worker_module, "AnkiService", MagicMock())
        monkeypatch.setattr(
            backfill_worker_module,
            "create_shared_lookup_services",
            MagicMock(return_value=shared_lookup),
        )
        monkeypatch.setattr(backfill_worker_module, "scan_backfill", scan)
        worker = BackfillScanWorker(test_config, options)
        errors: list[str] = []
        worker.error.connect(errors.append)

        worker.run()

        assert errors == [
            "Backfill scan failed: Dictionary 'Old Dictionary' needs reimport "
            "(schema upgrade) — Settings → Dictionaries → Reimport All"
        ]
        scan.assert_not_called()
        shared_lookup.close.assert_called_once_with()


class TestBackfillApplyWorker:
    def test_cancel_before_apply_still_emits_terminal_result(self, qtbot, test_config):
        with patch(f"{_WORKER_MOD}.AnkiService") as anki_cls:
            worker = BackfillApplyWorker(test_config, _PLAN)
            emitted: list[BackfillResult] = []
            cancelled: list[bool] = []
            worker.result_ready.connect(emitted.append)
            worker.cancelled.connect(lambda: cancelled.append(True))
            worker.cancel()
            worker.run()

        assert emitted == [BackfillResult(0, 0, 0, 0)]
        assert cancelled == [True]
        anki_cls.assert_not_called()

    def test_backfill_cancel_reaches_terminal_state(self, qtbot, test_config):
        tab = CardBackfillTab(test_config)
        qtbot.addWidget(tab)
        plan = _PLAN
        result = BackfillResult(notes_updated=1, fields_filled=2, tagged=1, skipped_stale=0)

        def fake_apply(anki, plan, *, tag, progress, is_cancelled):
            worker.cancel()
            return result

        with (
            patch(f"{_WORKER_MOD}.AnkiService"),
            patch(f"{_WORKER_MOD}.apply_backfill", side_effect=fake_apply),
        ):
            worker = BackfillApplyWorker(test_config, plan)
            cancelled: list[bool] = []
            worker.result_ready.connect(tab._on_apply_finished)
            worker.cancelled.connect(tab._on_apply_cancelled)
            worker.cancelled.connect(lambda: cancelled.append(True))
            worker.finished.connect(tab._on_worker_finished)
            tab._plan = plan
            tab.worker_thread = worker
            tab._set_running(True)
            tab.status_label.setText("Cancelling…")
            with qtbot.waitSignal(worker.finished, timeout=5000):
                worker.start()
            worker.wait(5000)

        assert tab._plan is None
        assert not tab.apply_button.isEnabled()
        assert tab.status_label.text() != "Cancelling…"
        assert "1" in tab.status_label.text()
        assert cancelled == [True]

    def test_cancelled_exception_clears_plan_and_reaches_terminal_state(self, qtbot, test_config):
        tab = CardBackfillTab(test_config)
        qtbot.addWidget(tab)

        def fake_apply(anki, plan, *, tag, progress, is_cancelled):
            worker.cancel()
            raise RuntimeError("failed after cancel")

        with (
            patch(f"{_WORKER_MOD}.AnkiService"),
            patch(f"{_WORKER_MOD}.apply_backfill", side_effect=fake_apply),
        ):
            worker = BackfillApplyWorker(test_config, _PLAN)
            assert hasattr(worker, "cancelled"), "cancelled apply needs an explicit terminal signal"
            results: list[BackfillResult] = []
            errors: list[str] = []
            cancelled: list[bool] = []
            worker.result_ready.connect(results.append)
            worker.result_ready.connect(tab._on_apply_finished)
            worker.cancelled.connect(tab._on_apply_cancelled)
            worker.cancelled.connect(lambda: cancelled.append(True))
            worker.error.connect(errors.append)
            worker.error.connect(tab._on_worker_error)
            worker.finished.connect(tab._on_worker_finished)
            tab._plan = _PLAN
            tab.worker_thread = worker
            tab._set_running(True)
            tab.status_label.setText("Cancelling…")
            with qtbot.waitSignal(worker.finished, timeout=5000):
                worker.start()
            worker.wait(5000)

        assert tab._plan is None
        assert not tab.apply_button.isEnabled()
        assert tab.status_label.text() != "Cancelling…"
        assert results == []
        assert errors == []
        assert cancelled == [True]

    def test_emits_result_on_success(self, qtbot, test_config):
        with (
            patch(f"{_WORKER_MOD}.AnkiService") as anki_cls,
            patch(f"{_WORKER_MOD}.create_shared_lookup_services") as factory,
            patch(f"{_WORKER_MOD}.apply_backfill", return_value=_RESULT) as apply_fn,
        ):
            worker = BackfillApplyWorker(test_config, _PLAN)
            with qtbot.waitSignal(worker.result_ready, timeout=5000) as blocker:
                worker.start()
            worker.wait(5000)
        assert blocker.args == [_RESULT]
        # Apply writes precomputed values: only AnkiService, never the factory.
        anki_cls.assert_called_once_with(test_config)
        factory.assert_not_called()
        assert apply_fn.call_args[0][1] is _PLAN

    def test_emits_error_on_failure(self, qtbot, test_config):
        with (
            patch(f"{_WORKER_MOD}.AnkiService"),
            patch(f"{_WORKER_MOD}.apply_backfill", side_effect=RuntimeError("boom")),
        ):
            worker = BackfillApplyWorker(test_config, _PLAN)
            with qtbot.waitSignal(worker.error, timeout=5000) as blocker:
                worker.start()
            worker.wait(5000)
        assert "boom" in blocker.args[0]

    def test_progress_signal_forwarded(self, qtbot, test_config):
        def fake_apply(anki, plan, *, tag, progress, is_cancelled):
            progress(1, 2)
            return _RESULT

        with (
            patch(f"{_WORKER_MOD}.AnkiService"),
            patch(f"{_WORKER_MOD}.apply_backfill", side_effect=fake_apply),
        ):
            worker = BackfillApplyWorker(test_config, _PLAN)
            seen: list[tuple[int, int]] = []
            worker.progress.connect(lambda done, total: seen.append((done, total)))
            with qtbot.waitSignal(worker.result_ready, timeout=5000):
                worker.start()
            worker.wait(5000)
        assert (1, 2) in seen
