"""Shared base for the three sequential mining queue workers.

:class:`YouTubeQueueWorker`, :class:`ReadingQueueWorker`, and
:class:`AudiobookQueueWorker` are structurally the same worker: each drives a
frozen snapshot of queue items through mining one at a time, emits the identical
four-signal shape, validates the processor-XOR-factory constructor contract, and
opens ``run()`` with the same schema-staleness pre-loop gate + deferred factory
build. This base owns that spine so the three subclasses carry only their
per-item body (``_run_item`` / ``_mine_one``) and per-tab extras.

Signal shape (declared here, inherited by every subclass — PyQt6 propagates
signals to subclasses):

* ``item_started(int)`` — idx, fired once before an item is mined. Items removed
  mid-run via :meth:`try_skip_item` are silently skipped: no signals for them.
* ``item_progress(int, str)`` — idx, label. Text only: an item's own completion
  fraction is not knowable, so none is published.
* ``item_warning(int, str, str)`` — idx, item description, recoverable error.
* ``item_retrying(int, int, int, int)`` — idx, next attempt, maximum, seconds
  left before that attempt starts. The backoff between automatic attempts, said
  out loud (D30).
* ``item_finished(int, object, object, int)`` — idx, result-or-None,
  error-string-or-None, attempts. Fires exactly once per item that runs.
* ``run_paused()`` / ``run_resumed()`` — the run stopped at, and left, an item
  boundary (D29).
* ``queue_finished()`` — fires once at the bottom of ``run()`` unless a subclass
  ``_run_item`` requested an early return (YouTube's mid-fetch cancellation).

The skip channel lives here so all three workers share it; without it a GUI-side
removal alone would still mine the item, because ``run()`` iterates the frozen
constructor snapshot, not the live GUI queue.

Two run-level policies also live here because all three workers need the same
answer:

* **Bounded automatic retry (D30-B).** A source-proven transient failure is
  re-attempted up to :data:`MAX_ATTEMPTS` times with a visible countdown. The
  hazard the classification exists to contain is an item that failed *after* it
  had already written notes to Anki: repeating that duplicates the user's cards.
  So eligibility is decided by explicit classification, never by "the error
  looked temporary" — see :func:`result_retry_eligible` and
  :meth:`SequentialQueueWorker._exception_retryable`.
* **Boundary-only pause (D29-A).** Pause, Finish-current and Resume are consumed
  between items, never inside ffmpeg, SQLite, media extraction or curation. That
  half lives on :class:`RunBoundaryControls`, which the batch queue worker also
  mixes in so all four list-driving workers answer the two verbs identically.
  The gate is opened unconditionally by every ``cancel()``, because a gate left
  closed is a shutdown deadlock.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, TypeVar

from PyQt6.QtCore import pyqtSignal

from anki_miner.exceptions import SetupError
from anki_miner.exceptions.youtube import (
    BotDetectionError,
    CookieDatabaseLockedError,
    DubAudioUnavailableError,
    NoJapaneseSubtitlesError,
    VideoTooLongError,
    YouTubeFetchError,
    YtdlpNotFoundError,
)
from anki_miner.gui.workers.base_worker import ProcessorOwningWorker
from anki_miner.models import AnkiWriteState, MiningOutcome, classify_result
from anki_miner.services.anki_service import is_transient_anki_transport_error

if TYPE_CHECKING:
    from anki_miner.config import AnkiMinerConfig
    from anki_miner.orchestration import EpisodeProcessor

logger = logging.getLogger(__name__)

# Queue item type — the concrete dataclass a subclass drives (all three are
# ``@dataclass(eq=False)`` so the skip set keys on identity).
ItemT = TypeVar("ItemT")

#: Total attempts a retry-eligible failure gets, first attempt included. Three
#: is what D30-B names in the row text ("Attempt 2 of 3"), and the point of a
#: bound is that a queue cannot spend the night on one broken item.
MAX_ATTEMPTS = 3

#: Seconds between automatic attempts. Long enough for the five-second network
#: blip that motivated D30-B to clear, short enough that the wait reads as a
#: wait rather than a stall. Tests set the per-worker override to zero.
RETRY_DELAY_S = 8.0

#: How often a paused run re-checks its gate. It is asleep either way; the poll
#: only bounds how long a cancel can take to be noticed.
_PAUSE_POLL_S = 0.1

# The progress adapter is constructed inside each concrete worker's item body,
# while the warning signal lives here. Run context bridges those existing call
# sites without making every worker duplicate the same callback argument.
_active_queue_run = threading.local()


def _emit_active_queue_warning(idx: int, item_description: str, error_message: str) -> None:
    """Emit a recoverable warning on the queue worker running this thread."""
    worker = getattr(_active_queue_run, "worker", None)
    if worker is not None:
        worker.item_warning.emit(idx, item_description, error_message)


#: Fetch failures that are *deterministic*: a second download costs another full
#: transfer and fails identically. Each is a :class:`YouTubeFetchError` subclass,
#: so they must be excluded explicitly before the generic clause below lets a
#: fetch error retry.
_DETERMINISTIC_FETCH_ERRORS: tuple[type[BaseException], ...] = (
    NoJapaneseSubtitlesError,
    BotDetectionError,
    CookieDatabaseLockedError,
    VideoTooLongError,
    YtdlpNotFoundError,
    DubAudioUnavailableError,
)


def queue_preflight_error(
    card_target_check: Callable[[], None],
    offline_dictionary_check: Callable[[], None],
) -> str | None:
    """Run ordered queue-level setup checks and return an actionable error."""
    try:
        card_target_check()
        offline_dictionary_check()
    except SetupError as exc:
        return str(exc)
    return None


def result_retry_eligible(result: object) -> bool:
    """Whether a non-raising failed ``process_*`` return may be repeated.

    Delegates to :attr:`ProcessingResult.auto_retry_eligible`, which requires a
    source-proven transient cause AND an exact
    :attr:`AnkiWriteState.NO_NOTE_WRITE`. The ``is True`` comparison is
    deliberate: a ``MagicMock`` stand-in's auto-generated attribute is truthy,
    and a truthy stub must never unlock a retry that could duplicate cards.
    """
    return getattr(result, "auto_retry_eligible", False) is True


@dataclass(frozen=True)
class AttemptOutcome:
    """What one attempt at mining a queue item produced.

    Kept separate from the emitted ``item_finished`` payload because the retry
    loop needs one more fact than the GUI does — whether repeating this attempt
    is *safe*, not merely whether it failed.
    """

    #: The ``ProcessingResult`` a non-raising attempt returned, if any.
    result: object | None = None
    #: Display text for a raised failure. ``None`` when nothing was raised.
    error: str | None = None
    #: Whether another automatic attempt is permitted. Fail-closed default.
    retryable: bool = False
    #: Abandon the whole queue without emitting ``item_finished`` or
    #: ``queue_finished`` (YouTube's mid-fetch cancellation).
    abort_queue: bool = False

    @property
    def failed(self) -> bool:
        """Whether this attempt did not produce a usable mined result."""
        if self.error is not None:
            return True
        return classify_result(self.result) is MiningOutcome.FAILED


class _MemoizedCuration:
    """One curation decision per item, reused by that item's later attempts.

    An automatic retry re-runs the whole pipeline, so without this the curator
    would reopen for every attempt: the user would be asked the same question
    three times, and the Known Words they staged on the first pass would be
    committed again. The memo also makes a cancelled curation terminal — there
    is no reading of "I stopped this" that means "so try it twice more".
    """

    def __init__(self, callback: Callable[[list], list | None]) -> None:
        self._callback = callback
        self._called = False
        self._value: list | None = None

    @property
    def is_terminal(self) -> bool:
        """True once the user cancelled curation for this item."""
        return self._called and self._value is None

    def __call__(self, words: list) -> list | None:
        """Ask once; every later attempt gets the identical answer back."""
        if not self._called:
            self._value = self._callback(words)
            self._called = True
        return self._value


class RunBoundaryControls:
    """Pause and Finish-current, consumed only between items (D29-A).

    Mixed into every worker that drives a list of items, so the batch worker and
    the three sequential queue workers answer the two boundary verbs the same
    way rather than each inventing a gate.

    The subclass declares the two signals itself -- PyQt6 only binds a
    ``pyqtSignal`` declared on a ``QObject`` subclass -- and calls
    :meth:`_init_boundary_controls` from its constructor. It must also release
    the gate from its ``cancel()``: a paused worker blocked on a closed gate
    never sees the cancel, and a bounded shutdown join then spends its whole
    timeout before retaining a laggard thread.
    """

    #: Supplied by ``CancellableWorker``. ``run_paused`` / ``run_resumed`` are
    #: deliberately left unannotated: they are ``pyqtSignal``s on the concrete
    #: worker, and a mixin-level annotation would collide with that declaration.
    is_cancelled: bool

    def _init_boundary_controls(self) -> None:
        """Open the gate and clear both requests. Call from ``__init__``."""
        self._pause_requested = threading.Event()
        self._stop_after_current = threading.Event()
        self._stop_claim_lock = threading.Lock()
        self._resume_gate = threading.Event()
        self._resume_gate.set()
        self._paused = threading.Event()

    def request_pause_after_current(self) -> None:
        """Stop cleanly at the next item boundary, keeping the run alive."""
        self._pause_requested.set()

    def resume(self) -> None:
        """Continue a paused run, or withdraw a pause that has not landed yet."""
        self._pause_requested.clear()
        self._resume_gate.set()

    def request_stop_after_current(self) -> None:
        """Let the current item finish, then end the run.

        Distinct from Cancel, which abandons the item in flight (D22 keeps that
        one verb prompt-free). The gate is opened too, so a run already paused
        stops instead of waiting for a Resume that is not coming.
        """
        with self._stop_claim_lock:
            self._stop_after_current.set()
            self._resume_gate.set()

    @property
    def is_paused(self) -> bool:
        """Whether the run is currently sitting at an item boundary."""
        return self._paused.is_set()

    @property
    def pause_pending(self) -> bool:
        """Whether a pause has been asked for but not yet reached a boundary."""
        return self._pause_requested.is_set()

    def _release_boundary_gate(self) -> None:
        """Unblock a paused run. Called from the worker's ``cancel()``."""
        self._resume_gate.set()

    def _wait_at_boundary(self) -> bool:
        """Honour Pause / Finish-current before the next item is claimed.

        This is the ONLY place either request is consumed, which is what makes
        the guarantee in D29-A true: a pause never lands inside ffmpeg, a
        SQLite write, media extraction or an open curator, so the progress
        numbers, the lock state and the receipt stay consistent with each other.

        Returns:
            ``False`` when the run must stop here (cancelled, or asked to finish
            after the item that just completed).
        """
        if self._stop_after_current.is_set():
            return False
        if self._pause_requested.is_set():
            self._pause_requested.clear()
            self._resume_gate.clear()
            self._paused.set()
            self.run_paused.emit()  # type: ignore[attr-defined]
        while not self._resume_gate.is_set():
            if self.is_cancelled:
                break
            self._resume_gate.wait(_PAUSE_POLL_S)
        if self._paused.is_set():
            self._paused.clear()
            if not self.is_cancelled and not self._stop_after_current.is_set():
                self.run_resumed.emit()  # type: ignore[attr-defined]
        return not self.is_cancelled and not self._stop_after_current.is_set()


class SequentialQueueWorker(RunBoundaryControls, ProcessorOwningWorker, Generic[ItemT]):
    """Base for workers that mine a queue of items one at a time.

    Subclasses parametrize the item type (``SequentialQueueWorker[FooItem]``)
    and MUST override:

    * :meth:`_stale_reimport_message` — the pre-loop staleness gate, resolving
      ``stale_resource_reimport_error`` in the *subclass* module so per-module test
      patches keep intercepting it.
    * :meth:`_run_item` — mine one item, emitting ``item_started`` /
      ``item_finished`` (and any per-item lifecycle the subclass owns). Return
      ``True`` to abort ``run()`` early *without* emitting ``queue_finished``
      (only YouTube's mid-fetch cancel uses this); ``False`` otherwise.
    """

    # Per-item index; emitted once before that item is mined.
    item_started = pyqtSignal(int)
    # (idx, label). No percentage: the queue bar counts finished items, and the
    # label carries the stage plus the true count inside it.
    item_progress = pyqtSignal(int, str)
    # (idx, item description, error). A nonfatal warning stays separate from
    # progress and from the item's eventual terminal classification.
    item_warning = pyqtSignal(int, str, str)
    # (idx, next_attempt, maximum, remaining_seconds). One per countdown second,
    # so the wait between automatic attempts is visible instead of looking like
    # a stall (D30-B).
    item_retrying = pyqtSignal(int, int, int, int)
    # (idx, result|None, error|None, attempts). Fires exactly once per item
    # that runs to completion (success or terminal failure).
    item_finished = pyqtSignal(int, object, object, int)
    # The run stopped at an item boundary, and later left it again (D29-A).
    run_paused = pyqtSignal()
    run_resumed = pyqtSignal()
    # Fires once after the last item, unless a subclass _run_item returned early.
    queue_finished = pyqtSignal()

    def __init__(
        self,
        processor: EpisodeProcessor | None,
        config: AnkiMinerConfig,
        items: list[ItemT],
        curation_callback: Callable[[list], list | None] | None,
        parent=None,
        *,
        processor_factory: Callable[[], EpisodeProcessor] | None = None,
    ) -> None:
        """Initialize the queue worker.

        Args:
            processor: Episode processor instance, or None when
                ``processor_factory`` is provided (built at run() start).
            config: Frozen app config.
            items: Queue items to process, in order (frozen snapshot).
            curation_callback: Forwarded to the processor. Pass ``None`` to
                disable entirely (the tab gates on its review checkbox).
            parent: Optional parent QObject.
            processor_factory: Zero-arg callable that returns an EpisodeProcessor.
                Mutually exclusive with a non-None ``processor``. When supplied,
                the processor is constructed on the worker thread inside run(),
                keeping the GUI thread free of the registry/sqlite/CSV work.
        """
        self._validate_processor_xor_factory(processor, processor_factory)
        super().__init__(parent)
        self._processor = processor
        self._processor_factory = processor_factory
        self._config = config
        self._items = items
        self._curation_callback = curation_callback
        # Skip channel: items the user removed mid-run (Clear / row [x]).
        # The run loop iterates the frozen constructor snapshot, so a GUI-side
        # removal alone would still mine the item — cards for rows that no
        # longer exist. Identity-based membership (queue items are eq=False);
        # ``self._items`` keeps every snapshot item alive, so identities are
        # stable for the whole run.
        self._skip_lock = threading.Lock()
        self._skipped: set[ItemT] = set()
        self._claimed: set[ItemT] = set()
        # Per-worker so a test can make the backoff instant without patching a
        # module constant the production path depends on.
        self._retry_delay_s = RETRY_DELAY_S
        # Live only for the duration of one item's attempt cycle.
        self._item_curation: _MemoizedCuration | None = None
        # D29 boundary controls. The gate starts open; only a consumed pause
        # request closes it, and cancel() always reopens it.
        self._init_boundary_controls()

    def cancel(self) -> None:
        """Request cancellation, always releasing the pause gate."""
        super().cancel()
        self._release_boundary_gate()

    @property
    def curation_processor(self) -> EpisodeProcessor | None:
        """The processor shared by every queue item.

        None before run() has built it via a supplied ``processor_factory``;
        the GUI caches it back after the run so subsequent runs reuse it.
        """
        return self._processor

    def try_skip_item(self, item: ItemT) -> bool:
        """Atomically skip an unclaimed item; refuse an item being mined.

        The GUI must remove the row only when this returns ``True``. Claim and
        skip share ``_skip_lock``, so Clear cannot remove an item after the
        worker has decided to mine it. Skipped items emit no signals at all.
        """
        with self._skip_lock:
            if item in self._claimed:
                return False
            self._skipped.add(item)
            return True

    def _try_claim_item(self, item: ItemT) -> bool:
        """Atomically claim and mark *item* unless Stop or Clear won first."""
        with self._stop_claim_lock:
            if self._stop_after_current.is_set():
                return False
            with self._skip_lock:
                if item in self._skipped:
                    return False
                self._claimed.add(item)
                self._mark_item_claimed(item)
                return True

    def run(self) -> None:
        """Process the queue end-to-end.

        Template method: the pre-loop staleness gate, deferred factory build,
        and the cancel/skip loop scaffolding live here; the subclass
        :meth:`_run_item` supplies the per-item body.
        """
        previous = getattr(_active_queue_run, "worker", None)
        _active_queue_run.worker = self
        try:
            try:
                self._run_queue()
            except Exception as exc:  # noqa: BLE001 - QThread.run exception boundary
                logger.exception("%s run failed", type(self).__name__)
                self.error.emit(f"{type(exc).__name__}: {exc}")
                self.queue_finished.emit()
        finally:
            if previous is None:
                del _active_queue_run.worker
            else:
                _active_queue_run.worker = previous

    def _run_queue(self) -> None:
        """Run queue logic inside :meth:`run`'s exception boundary."""
        # Schema-staleness pre-loop gate: abort the whole queue once with a
        # single actionable error when an enabled indexed dict slot needs
        # reimport — before any mining — instead of one silent zero-card
        # failure row per queued item.
        stale_msg = self._stale_reimport_message()
        if stale_msg is not None:
            self.error.emit(stale_msg)
            self.queue_finished.emit()
            return
        # Build the processor on the worker thread when a factory was supplied,
        # keeping the GUI thread free of the slow registry/sqlite/CSV work during
        # EpisodeProcessor construction. A factory failure ends the whole run:
        # emit error, then queue_finished so the tab recovers like any exit path.
        if self._processor is None:
            assert self._processor_factory is not None  # validated in __init__
            try:
                self._processor = self._processor_factory()
            except Exception as exc:  # noqa: BLE001 - surface every failure to GUI
                logger.exception("%s processor build failed", type(self).__name__)
                self.error.emit(f"{type(exc).__name__}: {exc}")
                self.queue_finished.emit()
                return
        if self.is_cancelled:
            self.queue_finished.emit()
            return
        preflight_error = queue_preflight_error(
            self._processor._preflight_card_target,
            self._processor.check_offline_dictionary,
        )
        if preflight_error is not None:
            self.error.emit(preflight_error)
            self.queue_finished.emit()
            return
        for idx, item in enumerate(self._items):
            if self.is_cancelled:
                break
            if not self._wait_at_boundary():
                break
            if not self._try_claim_item(item):
                continue  # removed from the GUI mid-run; no signals for it
            if self._run_item(idx, item):
                # Subclass requested an early return (YouTube mid-fetch cancel)
                # that suppresses queue_finished entirely.
                return
        self.queue_finished.emit()

    # ------------------------------------------------------------------
    # Bounded automatic retry (D30-B)
    # ------------------------------------------------------------------

    @property
    def _active_curation_callback(self) -> Callable[[list], list | None] | None:
        """The curation callback this attempt must use.

        Inside an attempt cycle this is the memo, so the three attempts of one
        item share a single curator decision. Outside one it is the raw callback,
        so nothing changes for a caller that drives ``_mine_one`` directly.
        """
        if self._curation_callback is None:
            return None
        return self._item_curation or self._curation_callback

    def _attempt_cycle(
        self,
        idx: int,
        attempt_fn: Callable[[], AttemptOutcome],
    ) -> tuple[AttemptOutcome, int]:
        """Run ``attempt_fn`` until it succeeds, is unsafe to repeat, or runs out.

        Args:
            idx: Queue index, carried on the countdown signal.
            attempt_fn: One whole attempt at the item. Must not raise.

        Returns:
            The last outcome and how many attempts were actually made.
        """
        memo = _MemoizedCuration(self._curation_callback) if self._curation_callback is not None else None
        self._item_curation = memo
        try:
            outcome = AttemptOutcome()
            attempt = 0
            for attempt in range(1, MAX_ATTEMPTS + 1):
                outcome = attempt_fn()
                if outcome.abort_queue or not outcome.failed or not outcome.retryable:
                    return outcome, attempt
                if attempt == MAX_ATTEMPTS or self.is_cancelled:
                    return outcome, attempt
                if memo is not None and memo.is_terminal:
                    # The user cancelled the curator for this item. Repeating it
                    # would either re-ask a question already answered "no" or
                    # mine words nobody approved.
                    return outcome, attempt
                if not self._wait_before_retry(idx, attempt + 1):
                    return outcome, attempt
            return outcome, attempt
        finally:
            self._item_curation = None

    def _wait_before_retry(self, idx: int, next_attempt: int) -> bool:
        """Count the backoff down out loud, abandoning it the moment Cancel lands.

        Returns:
            ``True`` when the wait completed and the next attempt may start.
        """
        remaining = int(round(self._retry_delay_s))
        if remaining <= 0:
            self.item_retrying.emit(idx, next_attempt, MAX_ATTEMPTS, 0)
            return not self.is_cancelled
        while remaining > 0:
            self.item_retrying.emit(idx, next_attempt, MAX_ATTEMPTS, remaining)
            # Waiting on the cancel event rather than sleeping is the whole
            # difference between "Cancel stops now" and "Cancel sits out the
            # timer it happened to land in".
            if self._cancel_event.wait(1.0):
                return False
            remaining -= 1
        return True

    def _classify_return(self, result: object) -> AttemptOutcome:
        """Wrap a non-raising ``process_*`` return with its retry verdict."""
        return AttemptOutcome(result=result, retryable=result_retry_eligible(result))

    def _classify_exception(self, exc: BaseException, message: str | None = None) -> AttemptOutcome:
        """Wrap a raised failure with its retry verdict.

        Args:
            exc: The exception the attempt raised.
            message: Display text, when the caller has a better one than the
                ``Type: text`` default (reading's crafted ``SetupError`` copy).
        """
        return AttemptOutcome(
            error=message if message is not None else f"{type(exc).__name__}: {exc}",
            retryable=self._exception_retryable(exc),
        )

    def _exception_retryable(self, exc: BaseException) -> bool:
        """Whether repeating the attempt that raised ``exc`` is safe and useful.

        Classified explicitly, class by class, and closed by default — an
        unrecognised exception is never repeated. Two classes qualify:

        * A **generic** :class:`YouTubeFetchError`. Fetching runs to completion
          before mining begins, so no ``addNotes`` can have happened for this
          item; the deterministic subclasses are excluded first because for them
          a second attempt only pays for a second download.
        * A **source-proven transient AnkiConnect transport failure**, and then
          only while the live service can still prove
          :attr:`AnkiWriteState.NO_NOTE_WRITE`. A connection dropped *during*
          ``addNotes`` is equally transient and must never be replayed.

        Setup errors, cancellation, malformed or Anki-side responses and every
        generic exception fall through to ``False``.
        """
        if isinstance(exc, (*_DETERMINISTIC_FETCH_ERRORS, SetupError)):
            return False
        if isinstance(exc, YouTubeFetchError):
            return True
        if is_transient_anki_transport_error(exc):
            return self._anki_write_state() is AnkiWriteState.NO_NOTE_WRITE
        return False

    def _anki_write_state(self) -> AnkiWriteState:
        """Fail-closed read of what the live processor can prove about writes.

        Anything that is not a real :class:`AnkiWriteState` — an absent service,
        a stub, a mock — has proved nothing, so it reports the answer that
        blocks the retry rather than the one that permits it.
        """
        service = getattr(self._processor, "anki_service", None)
        state = getattr(service, "anki_write_state", None)
        return state if isinstance(state, AnkiWriteState) else AnkiWriteState.NOTE_WRITE_UNCERTAIN

    # ------------------------------------------------------------------
    # Subclass hooks
    # ------------------------------------------------------------------

    def _stale_reimport_message(self) -> str | None:
        """Return the schema-staleness abort message, or None to proceed.

        Overridden per subclass to call that module's ``stale_resource_reimport_error``
        so ``patch("...<subclass>_queue_worker.stale_resource_reimport_error")`` keeps
        intercepting the check.
        """
        raise NotImplementedError

    def _run_item(self, idx: int, item: ItemT) -> bool:
        """Mine one item; emit ``item_started`` / ``item_finished``.

        Return ``True`` to abort ``run()`` without emitting ``queue_finished``
        (YouTube's mid-fetch cancellation); ``False`` to continue the queue.
        """
        raise NotImplementedError

    def _mark_item_claimed(self, item: ItemT) -> None:
        """Set the subclass-specific PROCESSING state under ``_skip_lock``."""
        raise NotImplementedError
