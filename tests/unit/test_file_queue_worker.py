"""Tests for the shared file-queue worker."""

from types import SimpleNamespace

from anki_miner.gui.widgets._tool_tab_base import _ToolTabBase
from anki_miner.gui.workers.file_queue_worker import FileQueueWorker
from anki_miner.models import TerminalOutcome


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


def test_success_then_fatal_queue_error_is_failed(qapp):
    worker = _SuccessThenFatalWorker()
    outcomes = []
    worker.queue_finished.connect(outcomes.append)

    worker.run()

    assert outcomes == [TerminalOutcome.FAILED]


def test_skipped_file_advances_authoritative_task_count():
    published: list[dict[str, object]] = []
    local: list[int] = []
    tab = SimpleNamespace(
        _item_total=lambda: 2,
        progress_widget=SimpleNamespace(set_percent=local.append),
        log_widget=SimpleNamespace(append_info=lambda _text: None),
        _strings=SimpleNamespace(skipped_prefix="Skipped: ", skipped="Skipped"),
        _publish_task_count=lambda **kwargs: published.append(kwargs),
    )

    _ToolTabBase._on_file_skipped(tab, 0, "/tmp/ep01.srt")

    assert local == [50]
    assert published == [{"current": 1, "total": 2, "detail": ""}]
