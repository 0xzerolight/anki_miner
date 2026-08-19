"""Tests for the shared file-queue worker."""

from types import SimpleNamespace

from anki_miner.gui.widgets._tool_tab_base import _ToolTabBase
from anki_miner.gui.workers.file_queue_worker import FileQueueWorker
from anki_miner.models import TerminalOutcome, classify_terminal_outcome


class _FatalQueueError(RuntimeError):
    pass


class _SuccessThenFatalWorker(FileQueueWorker):
    _FATAL_QUEUE_EXCEPTIONS = (_FatalQueueError,)

    def _queue_items(self):
        return ("success", "fatal")

    def _process_item(self, idx, item):
        if item == "fatal":
            raise _FatalQueueError("fatal queue error")
        self.file_finished.emit(idx, item, None)


class _ScriptedWorker(FileQueueWorker):
    """Emits per item according to a script of 'ok' / 'skip' / 'fail'."""

    def __init__(self, script):
        super().__init__()
        self._script = script

    def _queue_items(self):
        return self._script

    def _process_item(self, idx, item):
        if item == "skip":
            self.file_skipped.emit(idx, f"/tmp/out{idx}", "Skipped, exists")
        elif item == "fail":
            self.file_finished.emit(idx, None, "boom")
        else:
            self.file_finished.emit(idx, item, None)


def test_success_then_fatal_queue_error_is_failed(qapp):
    worker = _SuccessThenFatalWorker()
    outcomes = []
    worker.queue_finished.connect(outcomes.append)

    worker.run()

    assert outcomes == [TerminalOutcome.FAILED]


def test_skip_is_not_counted_as_success(qapp):
    """A skipped item lands in _skipped_count, never in _succeeded_count."""
    worker = _ScriptedWorker(["skip", "ok"])
    outcomes = []
    worker.queue_finished.connect(outcomes.append)

    worker.run()

    assert worker._succeeded_count == 1
    assert worker._skipped_count == 1
    assert worker._failed_count == 0
    assert outcomes == [TerminalOutcome.SUCCESS]


def test_all_skipped_queue_is_success_outcome(qapp):
    """Nothing failed, so an all-skipped run stays SUCCESS (the tab wording changes)."""
    worker = _ScriptedWorker(["skip", "skip"])
    outcomes = []
    worker.queue_finished.connect(outcomes.append)

    worker.run()

    assert worker._succeeded_count == 0
    assert worker._skipped_count == 2
    assert outcomes == [TerminalOutcome.SUCCESS]


def test_skip_plus_failure_stays_partial(qapp):
    """Skips keep counting toward 'not everything failed' for PARTIAL vs FAILED."""
    worker = _ScriptedWorker(["skip", "fail"])
    outcomes = []
    worker.queue_finished.connect(outcomes.append)

    worker.run()

    assert outcomes == [TerminalOutcome.PARTIAL]


def test_classify_terminal_outcome_skipped_is_neutral():
    assert classify_terminal_outcome(0, 0, skipped=3) is TerminalOutcome.SUCCESS
    assert classify_terminal_outcome(0, 1, skipped=2) is TerminalOutcome.PARTIAL
    assert classify_terminal_outcome(0, 1) is TerminalOutcome.FAILED
    assert classify_terminal_outcome(1, 1) is TerminalOutcome.PARTIAL


def test_skipped_file_advances_authoritative_task_count():
    published: list[dict[str, object]] = []
    local: list[int] = []
    logged: list[str] = []
    tab = SimpleNamespace(
        _item_total=lambda: 2,
        _run_skipped=0,
        progress_widget=SimpleNamespace(set_percent=local.append),
        log_widget=SimpleNamespace(append_info=logged.append),
        _strings=SimpleNamespace(skipped_prefix="Skipped: ", skipped="Skipped"),
        _publish_task_count=lambda **kwargs: published.append(kwargs),
    )

    _ToolTabBase._on_file_skipped(tab, 0, "/tmp/ep01.srt", "Skipped, exists")

    assert local == [50]
    # The reason replaces the old empty detail so Activity/global surfaces carry it.
    assert published == [{"current": 1, "total": 2, "detail": "Skipped, exists"}]
    assert logged == ["Skipped: ep01.srt — Skipped, exists"]
    assert tab._run_skipped == 1


def test_skipped_file_without_reason_keeps_bare_line():
    logged: list[str] = []
    tab = SimpleNamespace(
        _item_total=lambda: 1,
        _run_skipped=0,
        progress_widget=SimpleNamespace(set_percent=lambda _pct: None),
        log_widget=SimpleNamespace(append_info=logged.append),
        _strings=SimpleNamespace(skipped_prefix="Skipped: ", skipped="Skipped"),
        _publish_task_count=lambda **kwargs: None,
    )

    _ToolTabBase._on_file_skipped(tab, 0, "/tmp/ep01.srt", "")

    assert logged == ["Skipped: ep01.srt"]
