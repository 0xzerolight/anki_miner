"""Base class for cancellable worker threads."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from PyQt6.QtCore import QThread, pyqtSignal

from anki_miner.exceptions import AnkiMinerException, OperationCancelled
from anki_miner.utils.logging_ext import log_summary

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from anki_miner.orchestration.episode_processor import EpisodeProcessor


class CancellableWorker(QThread):
    """Base class for worker threads that support cancellation.

    Subclasses should:
    1. Call check_cancelled() at appropriate checkpoints in run()
    2. Stop processing when check_cancelled() returns True
    3. Emit error signal for exceptions (already defined here)

    Uses threading.Event for thread-safe cancellation flag.
    """

    # Signal emitted when an error occurs during processing
    error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancel_event = threading.Event()
        # Stamped by log_start; read by log_end and report_failure so an end or
        # failure record can state how long the run had been going. The class
        # name is the pre-start default so a worker that fails before log_start
        # still names itself.
        self._log_context = type(self).__name__
        self._log_started_at: float | None = None

    def cancel(self) -> None:
        """Request cancellation of the worker.

        This sets a thread-safe flag. The worker should check this flag
        at appropriate points and stop processing gracefully.
        """
        self._cancel_event.set()

    @property
    def is_cancelled(self) -> bool:
        """Check if cancellation has been requested.

        Returns:
            True if cancel() has been called
        """
        return self._cancel_event.is_set()

    def check_cancelled(self) -> bool:
        """Check if worker should stop processing.

        Call this at checkpoints in run(). If it returns True,
        stop processing and return from run().

        Returns:
            True if cancellation was requested
        """
        return self._cancel_event.is_set()

    def log_start(self, context: str, **fields: object) -> None:
        """Name this thread, then log its single boundary-entry receipt at INFO.

        Call exactly once at the top of ``run()``, before any work. This is not
        a progress hook and must never be called in a loop.

        The thread rename is what makes the ``[threadName]`` column in the log
        format worth having: without it every worker record reads ``Dummy-7``,
        so interleaved lines from two concurrent runs cannot be told apart. Not
        truncated — the whole context is the identity.

        The MAIN thread is never renamed. ``main()`` owns that name (it is how
        GUI-thread records are recognised), and many tests drive ``run()``
        inline on the main thread, where a rename would leak into every later
        record in the process.

        Like :meth:`report_failure`, this resolves the logger from the concrete
        instance so records keep the subclass's own module name instead of
        collapsing onto ``base_worker``. ``log_summary`` keeps every worker
        start line in the app's shared summary shape.

        Args:
            context: Worker identity for the stable ``<context> started:`` prefix.
            **fields: Bounded operation-shape fields for the start receipt.
        """
        current = threading.current_thread()
        if current is not threading.main_thread():
            current.name = context
        self._log_context = context
        self._log_started_at = time.monotonic()
        log = logging.getLogger(type(self).__module__)
        # ``log_summary`` reserves the keyword ``level``, so forwarding an
        # untyped ``**fields`` dict cannot be proven safe statically. Direct
        # callers with literal kwargs type-check without this.
        log_summary(log, f"{context} started", **fields)  # type: ignore[arg-type]

    def log_end(self, context: str | None = None, **fields: object) -> None:
        """Close the start receipt with the run's duration and its tallies.

        The pair matters more than either line: a start line with no end line is
        itself the diagnosis (the worker never returned), and a run's duration
        cannot be read off two timestamps when a log holds several interleaved
        runs. Emit it from a ``finally`` so a cancelled or failed run still
        closes its own receipt.

        Args:
            context: Overrides the identity stamped by :meth:`log_start`.
            **fields: Bounded outcome fields, normally the run's counts.
        """
        log = logging.getLogger(type(self).__module__)
        log_summary(
            log,
            f"{context or self._log_context} finished",
            elapsed_s=self.elapsed_s(),
            **fields,  # type: ignore[arg-type]
        )

    def elapsed_s(self) -> float | None:
        """Seconds since :meth:`log_start`, or None when it was never called."""
        if self._log_started_at is None:
            return None
        return round(time.monotonic() - self._log_started_at, 2)

    def report_failure(
        self,
        exc: BaseException,
        *,
        context: str,
        on_error: Callable[[str], None],
        on_cancelled: Callable[[], None] | None = None,
        cancel_flag_suppresses_error: bool = True,
    ) -> None:
        """Route a run() failure to the log and the GUI at the right volume.

        The one place worker catch-alls classify their failures, so an expected
        condition stops producing an ERROR-level traceback. Mirrors
        ``EpisodeProcessor.process_episode``: a typed ``AnkiMinerException`` is
        already a user-facing sentence, so it logs one WARNING line and no
        traceback; only genuinely unexpected exceptions get
        ``logger.exception``. Anki simply not being running used to write a
        40-line ``AnkiConnectionError`` traceback per attempt.

        Workers own different terminal signals (``error`` / ``failed`` /
        ``result_ready(False, msg)`` / an abstract ``_emit_error``), so the emit
        is injected rather than named here.

        No ``MemoryError`` re-raise branch, deliberately: ``EpisodeProcessor``
        re-raises it so it reaches *this* guard. Re-raising again would leave it
        unhandled out of ``QThread.run()``, where PyQt6 aborts the process. It
        is not an ``AnkiMinerException``, so it lands in the traceback-logging
        arm — the correct terminal handling.

        Args:
            exc: The caught exception.
            context: Worker identity for the log line (usually the class name).
            on_error: Called with ``str(exc)`` when the GUI must be told.
            on_cancelled: Called instead of ``on_error`` on the cancel path, for
                workers that declare a distinct ``cancelled`` signal.
            cancel_flag_suppresses_error: When True (the default), a worker whose
                cancel flag is set stays quiet about an unrelated exception --
                the user abandoned the run and does not need a dialog for it.
                Pass False where the terminal signal drives UI state that would
                otherwise hang, so a genuine failure still surfaces after a
                cancel (ImportWorker, DeckBuilderWorker). The log record is
                written either way.
        """
        # Per-instance logger so records keep the subclass's own module name
        # rather than collapsing onto base_worker.
        log = logging.getLogger(type(self).__module__)
        cancelled = self.check_cancelled()
        # How long the run had been going when it died. Appended rather than
        # inserted so every existing `<context> cancelled` / `<context>: <exc>`
        # grep still matches. Absent when log_start was never called.
        elapsed = self.elapsed_s()
        suffix = "" if elapsed is None else f" elapsed_s={elapsed}"
        # The TYPE is the cancel proof, not the flag and not the message text --
        # a worker can set its flag and then fail for an unrelated reason.
        if isinstance(exc, OperationCancelled) or (cancelled and cancel_flag_suppresses_error):
            log.info("%s cancelled%s", context, suffix)
            if on_cancelled is not None:
                on_cancelled()
            return
        if isinstance(exc, AnkiMinerException):
            log.warning("%s: %s%s", context, exc, suffix)
        else:
            # error(exc_info=exc), not exception(): identical output, but this
            # runs outside the handler that caught `exc` (ruff LOG004).
            log.error("%s unhandled exception%s", context, suffix, exc_info=exc)
        on_error(str(exc))


class ProcessorOwningWorker(CancellableWorker):
    """Base for mining workers that drive an :class:`EpisodeProcessor`.

    Declares the single typed contract for "the processor that owns
    curation/lookup resources". GUI readers — the curation dialog context
    builders and the Settings → Remove dictionary release hooks — consume
    only :attr:`curation_processor`, so a worker-side rename is a mypy
    error at the reader instead of a silent ``getattr`` miss (lookup_fn
    degrading to None, sqlite handles never closed).

    Subclasses MUST override the property to return their own processor
    storage (constructor arg or per-item current processor).
    """

    @staticmethod
    def _validate_processor_xor_factory(
        processor: EpisodeProcessor | None,
        processor_factory: Callable[[], EpisodeProcessor] | None,
        *,
        param_name: str = "processor",
    ) -> None:
        """Enforce the exactly-one-of processor/factory constructor contract.

        Shared by all processor-owning workers (episode, manual, and the three
        sequential queue workers): either a pre-built processor or a
        ``processor_factory`` is supplied, never both and never neither.
        ``param_name`` names the processor parameter in the message so
        :class:`ManualPairWorkerThread` keeps its ``episode_processor`` wording.
        """
        if processor is not None and processor_factory is not None:
            raise ValueError(f"Provide either {param_name} or processor_factory, not both")
        if processor is None and processor_factory is None:
            raise ValueError(f"Either {param_name} or processor_factory must be provided")

    @property
    def curation_processor(self) -> EpisodeProcessor | None:
        """Processor owning dictionary resources for the current or most recent run.

        ``None`` only when the worker has not created/received a processor
        yet (e.g. a queue worker before its first item).
        """
        raise NotImplementedError("ProcessorOwningWorker subclasses must override curation_processor")


class SingleCallWorker(CancellableWorker):
    """Run one blocking callable off the GUI thread and emit its result.

    Generic short-lived worker for "call a service method once, hand the
    result back on the main thread" tasks (e.g. the AnkiConnect fetchers).
    Cancellation is honored before the call and before the emit, so a
    cancel()'d worker stays silent.

    The result is carried as ``object`` so Qt's metatype system doesn't need a
    custom registration for whatever the callable returns. On failure, the
    inherited ``error`` signal carries ``f"{error_prefix}{exc}"``.
    """

    # Carries the callable's return value — typed as object (see class docstring).
    result_ready = pyqtSignal(object)

    def __init__(
        self,
        work: Callable[[], object] | Callable[[Callable[[], bool]], object],
        *,
        error_prefix: str = "",
        pass_cancel_check: bool = False,
        context: str | None = None,
        parent=None,
    ) -> None:
        """Initialize the single-call worker.

        Args:
            work: Callable executed in the background thread. With
                ``pass_cancel_check=True``, it receives a live cancellation
                predicate for checkpoints inside long work.
            error_prefix: Prepended to the exception text on the error signal.
            pass_cancel_check: Pass :meth:`check_cancelled` to ``work``.
            context: Worker identity for start and failure log records. Defaults
                to ``"SingleCallWorker"`` for compatibility.
            parent: Optional parent QObject for lifetime management.
        """
        super().__init__(parent)
        self._work = work
        self._error_prefix = error_prefix
        self._pass_cancel_check = pass_cancel_check
        self._context = context

    def run(self) -> None:
        """Execute the callable in the background thread and emit the result."""
        context = self._context or "SingleCallWorker"
        self.log_start(context)
        try:
            if self.check_cancelled():
                return

            if self._pass_cancel_check:
                cancellable_work = cast(Callable[[Callable[[], bool]], object], self._work)
                result = cancellable_work(self.check_cancelled)
            else:
                work = cast(Callable[[], object], self._work)
                result = work()

            if not self.check_cancelled():
                self.result_ready.emit(result)
        except Exception as e:  # noqa: BLE001 — surface every failure to GUI
            self.report_failure(
                e,
                context=context,
                on_error=lambda msg: self.error.emit(f"{self._error_prefix}{msg}"),
            )
