"""Bounded automatic retry (D30-B) and boundary-only pause (D29-A).

Both policies live on
:class:`~anki_miner.gui.workers._queue_worker_base.SequentialQueueWorker`, so
they are tested once here against one concrete subclass rather than three times
across the per-queue modules. ``AudiobookQueueWorker`` is the plainest of the
three: no fetch stage, no workspace allocation, one processor call per attempt.

The retry half is safety-critical rather than merely convenient. Every case
below exists because getting it wrong duplicates the user's Anki cards or
silently swallows a failure the user needed to see.
"""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests
from PyQt6.QtCore import Qt

from anki_miner.exceptions import AnkiConnectionError, SetupError
from anki_miner.exceptions.youtube import (
    BotDetectionError,
    CookieDatabaseLockedError,
    DubAudioUnavailableError,
    NoJapaneseSubtitlesError,
    VideoTooLongError,
    YouTubeFetchError,
    YtdlpNotFoundError,
)
from anki_miner.gui.workers._queue_worker_base import MAX_ATTEMPTS, result_retry_eligible
from anki_miner.gui.workers.audiobook_queue_worker import AudiobookQueueWorker
from anki_miner.models import AnkiWriteState, ProcessingResult
from anki_miner.models.audiobook_queue import AudiobookQueueItem
from anki_miner.models.mining_queue import ReadyItemStatus
from tests.unit._queue_worker_harness import connect_all, make_mock_processor

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_item(stem: str = "book") -> AudiobookQueueItem:
    """Build a READY audiobook queue item."""
    return AudiobookQueueItem(
        audio_file=Path(f"/tmp/{stem}.mp3"),
        subtitle_file=Path(f"/tmp/{stem}.srt"),
        status=ReadyItemStatus.READY,
    )


def _ok_result() -> ProcessingResult:
    """A successful, fully-stamped result."""
    return ProcessingResult(
        total_words_found=1,
        new_words_found=1,
        cards_created=1,
        anki_write_state=AnkiWriteState.NOTE_WRITE_CONFIRMED,
    )


def _failed_result(*, transient: bool, state: AnkiWriteState) -> ProcessingResult:
    """A failed result carrying exactly the provenance under test."""
    return ProcessingResult(
        total_words_found=0,
        new_words_found=0,
        cards_created=0,
        errors=["boom"],
        anki_write_state=state,
        failure_is_transient=transient,
    )


def _transient_anki_error() -> AnkiConnectionError:
    """The one shape :func:`is_transient_anki_transport_error` accepts."""
    exc = AnkiConnectionError("AnkiConnect unreachable")
    exc.__cause__ = requests.exceptions.ConnectionError("refused")
    return exc


def _raise_transient(*_args, **_kwargs):
    """``side_effect`` that actually raises — a function *returning* an
    exception makes Mock hand it back as a value instead."""
    raise _transient_anki_error()


@pytest.fixture
def processor():
    """MagicMock EpisodeProcessor whose AnkiService proves no note was written."""
    proc = make_mock_processor("process_episode", _ok_result())
    proc.anki_service.anki_write_state = AnkiWriteState.NO_NOTE_WRITE
    return proc


@pytest.fixture
def make_worker(qapp, processor, test_config):
    """Factory producing an AudiobookQueueWorker over ``items``."""

    def _make(items=None, curation_callback=None):
        return AudiobookQueueWorker(
            processor=processor,
            config=test_config,
            items=items if items is not None else [_make_item()],
            curation_callback=curation_callback,
        )

    return _make


# ---------------------------------------------------------------------------
# Exception classification — the matrix that decides whether cards can duplicate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        NoJapaneseSubtitlesError("none written"),
        BotDetectionError("sign in to confirm"),
        CookieDatabaseLockedError("locked"),
        VideoTooLongError("too long"),
        YtdlpNotFoundError("missing"),
        DubAudioUnavailableError("gone"),
    ],
    ids=lambda e: type(e).__name__,
)
def test_deterministic_fetch_subclasses_are_never_retried(make_worker, exc):
    """Each is a YouTubeFetchError subclass, so it must be excluded by name."""
    assert make_worker()._exception_retryable(exc) is False


def test_generic_fetch_error_is_retried(make_worker):
    """Fetching completes before mining, so a repeat cannot duplicate a note."""
    assert make_worker()._exception_retryable(YouTubeFetchError("blip")) is True


def test_setup_error_is_never_retried(make_worker):
    assert make_worker()._exception_retryable(SetupError("no deck")) is False


def test_generic_exception_is_never_retried(make_worker):
    assert make_worker()._exception_retryable(RuntimeError("who knows")) is False


def test_anki_error_without_transport_cause_is_never_retried(make_worker):
    """An AnkiConnect-side error payload re-runs to the same answer."""
    assert make_worker()._exception_retryable(AnkiConnectionError("bad request")) is False


def test_transient_anki_error_retried_only_while_nothing_was_written(make_worker, processor):
    worker = make_worker()
    exc = _transient_anki_error()

    processor.anki_service.anki_write_state = AnkiWriteState.NO_NOTE_WRITE
    assert worker._exception_retryable(exc) is True

    processor.anki_service.anki_write_state = AnkiWriteState.NOTE_WRITE_UNCERTAIN
    assert worker._exception_retryable(exc) is False

    processor.anki_service.anki_write_state = AnkiWriteState.NOTE_WRITE_CONFIRMED
    assert worker._exception_retryable(exc) is False


def test_unreadable_write_state_fails_closed(make_worker, processor):
    """A stub, a string or a missing service has proved nothing."""
    worker = make_worker()
    exc = _transient_anki_error()

    processor.anki_service.anki_write_state = "no_note_write"
    assert worker._exception_retryable(exc) is False

    del processor.anki_service
    assert worker._anki_write_state() is AnkiWriteState.NOTE_WRITE_UNCERTAIN


# ---------------------------------------------------------------------------
# Returned-result classification
# ---------------------------------------------------------------------------


def test_result_retry_eligibility_requires_both_halves():
    assert result_retry_eligible(_failed_result(transient=True, state=AnkiWriteState.NO_NOTE_WRITE)) is True
    assert result_retry_eligible(_failed_result(transient=False, state=AnkiWriteState.NO_NOTE_WRITE)) is False
    assert result_retry_eligible(_failed_result(transient=True, state=AnkiWriteState.NOTE_WRITE_UNCERTAIN)) is False
    assert result_retry_eligible(_failed_result(transient=True, state=AnkiWriteState.NOTE_WRITE_CONFIRMED)) is False


def test_truthy_mock_result_never_unlocks_a_retry():
    """A MagicMock's auto-generated attribute is truthy; it must still fail closed."""
    assert result_retry_eligible(MagicMock()) is False


def test_retryable_returned_failure_gets_three_attempts(make_worker, processor):
    processor.process_episode.return_value = _failed_result(transient=True, state=AnkiWriteState.NO_NOTE_WRITE)

    worker = make_worker()
    caps = connect_all(worker)
    worker.run()

    assert processor.process_episode.call_count == MAX_ATTEMPTS
    assert len(caps["finished"].calls) == 1
    assert caps["finished"].calls[0][3] == MAX_ATTEMPTS
    assert len(caps["queue_finished"].calls) == 1


def test_unsafe_returned_failure_is_not_repeated(make_worker, processor):
    """A failure that cannot prove it wrote nothing goes straight to Failed."""
    processor.process_episode.return_value = _failed_result(transient=True, state=AnkiWriteState.NOTE_WRITE_CONFIRMED)

    worker = make_worker()
    caps = connect_all(worker)
    worker.run()

    assert processor.process_episode.call_count == 1
    assert caps["finished"].calls[0][3] == 1
    assert caps["retrying"].calls == []


def test_retry_succeeds_on_the_second_attempt(make_worker, processor):
    ok = _ok_result()
    processor.process_episode.side_effect = [
        _failed_result(transient=True, state=AnkiWriteState.NO_NOTE_WRITE),
        ok,
    ]

    worker = make_worker()
    caps = connect_all(worker)
    worker.run()

    assert caps["finished"].calls == [(0, ok, None, 2)]


# ---------------------------------------------------------------------------
# Countdown
# ---------------------------------------------------------------------------


def test_countdown_states_every_remaining_second(make_worker, processor):
    processor.process_episode.side_effect = _raise_transient

    worker = make_worker()
    worker._retry_delay_s = 3
    caps = connect_all(worker)
    worker.run()

    # Two waits of three ticks each, counting down and naming the attempt about
    # to start. This is the "Attempt 2 of 3 · retrying in 8s" payload.
    assert caps["retrying"].calls == [
        (0, 2, 3, 3),
        (0, 2, 3, 2),
        (0, 2, 3, 1),
        (0, 3, 3, 3),
        (0, 3, 3, 2),
        (0, 3, 3, 1),
    ]


def test_cancel_during_backoff_stops_without_sitting_out_the_timer(qapp, processor, test_config):
    """Pressing Cancel mid-countdown must end the run now, not in eight seconds."""
    processor.process_episode.side_effect = _raise_transient

    worker = AudiobookQueueWorker(
        processor=processor,
        config=test_config,
        items=[_make_item("a"), _make_item("b")],
        curation_callback=None,
    )
    # Long enough that a wait actually sat out would be unmistakable.
    worker._retry_delay_s = 30
    caps = connect_all(worker, direct=True)
    # Direct too: an auto-connected plain callable would be queued onto the main
    # thread's event loop, which this test never spins.
    worker.item_retrying.connect(lambda *_: worker.cancel(), Qt.ConnectionType.DirectConnection)

    thread = threading.Thread(target=worker.run)
    thread.start()
    try:
        thread.join(5)
    finally:
        worker.cancel()
        thread.join(5)

    assert not thread.is_alive()
    # One attempt on item one, no second item, and no second countdown tick.
    assert processor.process_episode.call_count == 1
    assert len(caps["retrying"].calls) == 1
    assert caps["finished"].calls[0][3] == 1


# ---------------------------------------------------------------------------
# Curation memo
# ---------------------------------------------------------------------------


def test_three_attempts_open_the_curator_once(make_worker, processor):
    """Retry re-runs the pipeline; it must not re-ask, or re-commit Known Words."""
    asked: list[list] = []

    def _curation(words):
        asked.append(list(words))
        return words

    seen: list = []

    def _mine(*_args, **kwargs):
        cb = kwargs["curation_callback"]
        seen.append(cb(["word"]))
        return _failed_result(transient=True, state=AnkiWriteState.NO_NOTE_WRITE)

    processor.process_episode.side_effect = _mine

    worker = make_worker(curation_callback=_curation)
    worker.run()

    assert len(asked) == 1
    assert seen == [["word"], ["word"], ["word"]]


def test_cancelled_curation_is_terminal(make_worker, processor):
    """``None`` means the user stopped this item; three tries is not the answer."""

    def _mine(*_args, **kwargs):
        kwargs["curation_callback"](["word"])
        return _failed_result(transient=True, state=AnkiWriteState.NO_NOTE_WRITE)

    processor.process_episode.side_effect = _mine

    worker = make_worker(curation_callback=lambda _words: None)
    worker.run()

    assert processor.process_episode.call_count == 1


def test_memo_does_not_leak_between_items(make_worker, processor):
    """Each item gets its own decision; a manual retry gets a fresh one too."""
    asked: list[list] = []

    def _curation(words):
        asked.append(list(words))
        return words

    def _mine(*_args, **kwargs):
        kwargs["curation_callback"](["w"])
        return _ok_result()

    processor.process_episode.side_effect = _mine

    worker = make_worker(items=[_make_item("a"), _make_item("b")], curation_callback=_curation)
    worker.run()

    assert len(asked) == 2


# ---------------------------------------------------------------------------
# Boundary-only pause (D29-A)
# ---------------------------------------------------------------------------


class _ItemBarrier:
    """Let the test observe, and hold, the worker at a chosen item."""

    def __init__(self) -> None:
        self.started: list[str] = []
        self.reached = threading.Event()
        self.release = threading.Event()

    def __call__(self, audio, _subtitle, **_kwargs):
        self.started.append(Path(audio).stem)
        if len(self.started) == 1:
            self.reached.set()
            self.release.wait(5)
        return _ok_result()


def _run_in_thread(worker) -> threading.Thread:
    thread = threading.Thread(target=worker.run)
    thread.start()
    return thread


def test_pause_stops_before_the_next_item_and_resume_continues(qapp, processor, test_config):
    barrier = _ItemBarrier()
    processor.process_episode.side_effect = barrier

    worker = AudiobookQueueWorker(
        processor=processor,
        config=test_config,
        items=[_make_item("a"), _make_item("b")],
        curation_callback=None,
    )
    caps = connect_all(worker, direct=True)
    thread = _run_in_thread(worker)
    try:
        assert barrier.reached.wait(5)
        worker.request_pause_after_current()
        barrier.release.set()

        # The pause lands at the boundary, not inside item one.
        deadline = threading.Event()
        for _ in range(100):
            if worker.is_paused:
                break
            deadline.wait(0.02)
        assert worker.is_paused
        assert barrier.started == ["a"]
        assert len(caps["paused"].calls) == 1

        worker.resume()
        thread.join(5)
    finally:
        barrier.release.set()
        worker.cancel()
        thread.join(5)

    assert not thread.is_alive()
    assert barrier.started == ["a", "b"]
    assert len(caps["resumed"].calls) == 1
    assert len(caps["queue_finished"].calls) == 1


def test_cancel_while_paused_ends_the_run(qapp, processor, test_config):
    """A closed gate must never be able to outlive a Cancel — that is a deadlock."""
    barrier = _ItemBarrier()
    processor.process_episode.side_effect = barrier

    worker = AudiobookQueueWorker(
        processor=processor,
        config=test_config,
        items=[_make_item("a"), _make_item("b")],
        curation_callback=None,
    )
    caps = connect_all(worker, direct=True)
    thread = _run_in_thread(worker)
    try:
        assert barrier.reached.wait(5)
        worker.request_pause_after_current()
        barrier.release.set()
        for _ in range(100):
            if worker.is_paused:
                break
            threading.Event().wait(0.02)
        assert worker.is_paused

        worker.cancel()
        thread.join(5)
    finally:
        barrier.release.set()
        worker.cancel()
        thread.join(5)

    assert not thread.is_alive()
    assert barrier.started == ["a"]
    assert caps["resumed"].calls == []
    assert len(caps["queue_finished"].calls) == 1


def test_finish_current_then_stop_completes_the_item_and_ends(qapp, processor, test_config):
    barrier = _ItemBarrier()
    processor.process_episode.side_effect = barrier

    worker = AudiobookQueueWorker(
        processor=processor,
        config=test_config,
        items=[_make_item("a"), _make_item("b")],
        curation_callback=None,
    )
    caps = connect_all(worker, direct=True)
    thread = _run_in_thread(worker)
    try:
        assert barrier.reached.wait(5)
        worker.request_stop_after_current()
        barrier.release.set()
        thread.join(5)
        # Finish-current is not a cancellation: item one's result is real.
        stopped_without_cancelling = not worker.is_cancelled
    finally:
        barrier.release.set()
        worker.cancel()
        thread.join(5)

    assert not thread.is_alive()
    # Item one ran to a real result; item two never started.
    assert barrier.started == ["a"]
    assert stopped_without_cancelling
    assert len(caps["finished"].calls) == 1
    assert caps["finished"].calls[0][2] is None
    assert len(caps["queue_finished"].calls) == 1


def test_finish_current_between_boundary_check_and_claim_stops_next_item(make_worker, processor):
    """Finish-current must win when it lands before the next item is claimed."""
    first = _make_item("a")
    second = _make_item("b")
    worker = make_worker(items=[first, second])
    claim = worker._try_claim_item

    def _claim_after_stop(item):
        if item is second:
            worker.request_stop_after_current()
        return claim(item)

    worker._try_claim_item = _claim_after_stop

    worker.run()

    assert processor.process_episode.call_count == 1
    assert first in worker._claimed
    assert second not in worker._claimed
    assert second.status is ReadyItemStatus.READY


def test_finish_current_releases_a_paused_run(qapp, processor, test_config):
    barrier = _ItemBarrier()
    processor.process_episode.side_effect = barrier

    worker = AudiobookQueueWorker(
        processor=processor,
        config=test_config,
        items=[_make_item("a"), _make_item("b")],
        curation_callback=None,
    )
    caps = connect_all(worker, direct=True)
    thread = _run_in_thread(worker)
    try:
        assert barrier.reached.wait(5)
        worker.request_pause_after_current()
        barrier.release.set()
        for _ in range(100):
            if worker.is_paused:
                break
            threading.Event().wait(0.02)
        assert worker.is_paused

        worker.request_stop_after_current()
        thread.join(5)
    finally:
        barrier.release.set()
        worker.cancel()
        thread.join(5)

    assert not thread.is_alive()
    assert barrier.started == ["a"]
    assert caps["resumed"].calls == []


def test_cancel_during_processor_factory_skips_queue_preflight(qapp, test_config):
    processor = make_mock_processor("process_episode", _ok_result())
    worker = None

    def _factory():
        assert worker is not None
        worker.cancel()
        return processor

    worker = AudiobookQueueWorker(
        processor=None,
        config=test_config,
        items=[_make_item()],
        curation_callback=None,
        processor_factory=_factory,
    )
    caps = connect_all(worker)

    worker.run()

    processor._preflight_card_target.assert_not_called()
    processor.check_offline_dictionary.assert_not_called()
    processor.process_episode.assert_not_called()
    assert len(caps["queue_finished"].calls) == 1


def test_pause_requested_before_the_run_holds_the_first_item(qapp, processor, test_config):
    """The gate is checked before every claim, the first one included."""
    processor.process_episode.side_effect = lambda *a, **k: _ok_result()

    worker = AudiobookQueueWorker(
        processor=processor,
        config=test_config,
        items=[_make_item("a")],
        curation_callback=None,
    )
    worker.request_pause_after_current()
    thread = _run_in_thread(worker)
    try:
        for _ in range(100):
            if worker.is_paused:
                break
            threading.Event().wait(0.02)
        assert worker.is_paused
        assert processor.process_episode.call_count == 0
    finally:
        worker.cancel()
        thread.join(5)

    assert not thread.is_alive()
