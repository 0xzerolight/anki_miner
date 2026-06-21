"""Tests for the yt-dlp update worker thread."""

from __future__ import annotations

from anki_miner.gui.workers.ytdlp_update_worker import YtdlpUpdateWorker
from anki_miner.services.ytdlp_updater import YtdlpUpdateResult


class _StubUpdater:
    """Records the call and returns a canned result."""

    def __init__(self, result: object = None, *, raise_exc: Exception | None = None) -> None:
        self._result = result if result is not None else YtdlpUpdateResult(action="up_to_date")
        self._raise = raise_exc
        self.calls: list[dict] = []

    def check_and_update(self, *, force: bool = False, cancel=None) -> object:
        self.calls.append({"force": force, "cancel": cancel})
        if self._raise is not None:
            raise self._raise
        return self._result


def test_emits_result_ready(qtbot):
    result = YtdlpUpdateResult(action="installed", installed_version="2024.03.10")
    updater = _StubUpdater(result)
    worker = YtdlpUpdateWorker(updater, force=True)

    with qtbot.waitSignal(worker.result_ready, timeout=2000) as blocker:
        worker.start()
    worker.wait(2000)

    assert blocker.args[0] is result
    assert updater.calls == [{"force": True, "cancel": worker.check_cancelled}]


def test_exception_emits_error_not_raise(qtbot):
    updater = _StubUpdater(raise_exc=RuntimeError("boom"))
    worker = YtdlpUpdateWorker(updater, force=False)

    with qtbot.waitSignal(worker.error, timeout=2000) as blocker:
        worker.start()
    worker.wait(2000)

    assert "boom" in blocker.args[0]


def test_cancelled_before_run_emits_nothing(qtbot):
    updater = _StubUpdater()
    worker = YtdlpUpdateWorker(updater, force=False)
    worker.cancel()

    # No signal should fire; just ensure run() returns cleanly.
    with qtbot.assertNotEmitted(worker.result_ready):
        worker.start()
        worker.wait(2000)
    assert updater.calls == []
