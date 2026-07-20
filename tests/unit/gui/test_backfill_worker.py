"""Tests for gui/workers/backfill_worker.py (scan + apply workers)."""

from __future__ import annotations

from unittest.mock import patch

from anki_miner.gui.workers.backfill_worker import BackfillApplyWorker, BackfillScanWorker
from anki_miner.services.card_backfiller import BackfillOptions, BackfillPlan, BackfillResult

_OPTIONS = BackfillOptions(field_keys=frozenset({"frequency"}))
_PLAN = BackfillPlan(
    options=_OPTIONS,
    notes=(),
    scanned=0,
    skipped_no_identity=0,
    unavailable_fields=(),
    sentinel_only_sorts=0,
    expression_field="Expression",
)
_RESULT = BackfillResult(notes_updated=1, fields_filled=2, tagged=1, skipped_stale=0)

_WORKER_MOD = "anki_miner.gui.workers.backfill_worker"


class TestBackfillScanWorker:
    def test_emits_plan_on_success(self, qtbot, test_config):
        with (
            patch(f"{_WORKER_MOD}.AnkiService") as anki_cls,
            patch(f"{_WORKER_MOD}.create_services") as factory,
            patch(f"{_WORKER_MOD}.scan_backfill", return_value=_PLAN) as scan,
        ):
            worker = BackfillScanWorker(test_config, _OPTIONS)
            with qtbot.waitSignal(worker.result_ready, timeout=5000) as blocker:
                worker.start()
            worker.wait(5000)
        assert blocker.args == [_PLAN]
        # One AnkiService, injected into create_services (no second instance).
        anki_cls.assert_called_once_with(test_config)
        assert factory.call_args[1]["anki_service"] is anki_cls.return_value
        assert scan.call_args[0][0] is anki_cls.return_value

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
            patch(f"{_WORKER_MOD}.create_services"),
            patch(f"{_WORKER_MOD}.scan_backfill", return_value=_PLAN),
        ):
            worker = BackfillScanWorker(test_config, _OPTIONS)
            worker.cancel()
            emitted: list[object] = []
            worker.result_ready.connect(emitted.append)
            worker.start()
            worker.wait(5000)
        assert emitted == []


class TestBackfillApplyWorker:
    def test_emits_result_on_success(self, qtbot, test_config):
        with (
            patch(f"{_WORKER_MOD}.AnkiService") as anki_cls,
            patch(f"{_WORKER_MOD}.create_services") as factory,
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
