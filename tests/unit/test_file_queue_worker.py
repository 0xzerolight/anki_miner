"""Tests for the shared file-queue worker."""

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
