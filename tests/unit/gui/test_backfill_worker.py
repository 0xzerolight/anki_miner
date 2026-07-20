"""Tests for gui/workers/backfill_worker.py (scan + apply workers)."""

from __future__ import annotations

from unittest.mock import patch

from anki_miner.gui.widgets.backfill_tab import CardBackfillTab
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
    config_version=0,
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
