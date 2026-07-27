"""Reading-specific worker/processor lifecycle for the reading sub-tabs.

Both reading sub-tabs (manga, novels) — and the subtitles sub-tab — drive one
long-running :class:`~anki_miner.gui.workers.reading_queue_worker.ReadingQueueWorker`
mining a list of :class:`ReadingQueueItem` sequentially, over a single cached
:class:`~anki_miner.orchestration.episode_processor.EpisodeProcessor`. The
generic run lifecycle lives on
:class:`~anki_miner.gui.widgets._queue_mining_tab_base._QueueMiningTabBase`
(ARC-008); this reading subclass supplies only the reading worker, the reading
source detector, the terminal single-bar state, and the table-only curation
context. Each sub-tab supplies its own queue model, layout, progress widgets,
and button state.

The worker OWNS the item lifecycle (it sets ``status``/``cards_created``/
``error_message`` on each item, on the worker thread, before emitting its
signals), so a sub-tab's signal slots are READ-ONLY on item state: they refresh
the row display and summary counts, never write status/cards/error. A queued
``item_started`` slot arriving late must not overwrite a COMPLETED status back
to PROCESSING.

D8 (amended): reading curation has no player/subtitle media context (the
``ReadingQueueWorker`` publishes no ``_curation_video``/``_curation_subtitle``/
``_curation_offset``), so this base's :meth:`_build_curation_context` returns a
``None`` media context — but it DOES wire the definition pane's ``lookup_fn``
from the worker's ``curation_processor``, so novels and subtitles show word
meanings on row focus. The manga sub-tab overrides it to add a page-image
context (from the worker's published ``curation_document``) while keeping the
same lookup_fn.

This base deliberately does NOT wire :meth:`MiningTabBase._teardown_previous_run`.
Single-episode/batch tabs build a fresh processor per run, so teardown closes
the survivor's processor safely; reading caches ``self._processor`` and hands
the SAME object to the worker, so base teardown would close the cached
processor and break the next run. The ``worker_thread is not None`` early-return
guard in :meth:`_launch_run` plus the convergent :meth:`_on_worker_finished`
cleanup are the whole concurrency contract here.

**Subclass contract** — a concrete sub-tab (and the test fixture) MUST provide:

* ``self.review_words_checkbox`` — the curation opt-in checkbox; its
  ``isChecked()`` gates the curation callback in :meth:`_launch_run`.
* ``self.log_widget`` — a :class:`LogWidget`; :meth:`_launch_run` logs the run
  banner and wires ``worker.error`` to ``log_widget.append_error``.
* ``_on_item_started``/``_on_item_progress``/``_on_item_finished``/
  ``_on_queue_finished`` — the four worker-signal slots, dereferenced at
  ``.connect()`` time in :meth:`_launch_run`. They read item state via
  :meth:`_item_at` and stay READ-ONLY on it.
* ``_after_run_cleanup()`` — called from :meth:`_on_worker_finished` after the
  worker is nulled; the sub-tab restores its Stop button, resets its progress
  bar(s), and recomputes button state here.

Base ``_launch_run`` does NOT reset progress or recompute buttons — those are
per-tab UI concerns owned by the caller (which recomputes buttons after a
``True`` return; the novels tab has no dual-bar progress at all).

Internal-but-tested: this private module (leading underscore) has no public facade —
the reading manga/novels/subtitles tab tests and ``tests/unit/test_reading_mining_base.py``
import it directly. The underscore stays and the module path is a stable test surface;
do not rename it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PyQt6.QtCore import QCoreApplication

from anki_miner.exceptions import SetupError
from anki_miner.gui.utils.service_factory import create_episode_processor
from anki_miner.gui.widgets._queue_mining_tab_base import _QueueMiningTabBase, _QueueRunStrings
from anki_miner.gui.workers.reading_queue_worker import ReadingQueueWorker
from anki_miner.models import MiningOutcome, TerminalOutcome, classify_result, classify_terminal_outcome
from anki_miner.models.mining_queue import ReadyItemStatus
from anki_miner.services.reading import detector
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from PyQt6.QtWidgets import QWidget

    from anki_miner.config import AnkiMinerConfig
    from anki_miner.gui.widgets.dialogs.word_curation_dialog import CurationMediaContext
    from anki_miner.gui.workers._queue_worker_base import SequentialQueueWorker
    from anki_miner.interfaces.presenter import PresenterProtocol
    from anki_miner.models.reading import ReadingSourceRef
    from anki_miner.orchestration import EpisodeProcessor

logger = logging.getLogger(__name__)


class _ReadingMiningTabBase(_QueueMiningTabBase):
    """Worker/processor lifecycle shared by the manga and novels reading tabs.

    Owns at most one running :class:`ReadingQueueWorker` and a single cached
    :class:`EpisodeProcessor` reused across runs within the sub-tab (both via
    :class:`_QueueMiningTabBase`). Overrides :meth:`_build_curation_context` to
    inherit the definition-pane lookup_fn (from ``curation_processor``) with a
    ``None`` media context; the manga sub-tab overrides it further to add a
    page-image context.
    """

    _shutdown_log_name = "Reading"
    #: Name this screen's run carries in the task registry. Each sub-tab sets it
    #: with ``QT_TRANSLATE_NOOP("ReadingTab", ...)``, keeping the four literals
    #: in the one tr-context this family shares.
    TASK_TITLE: str = ""
    # Enable the promoted stranded-PROCESSING recovery sweep for reading too.
    _status_ready = ReadyItemStatus.READY
    _status_processing = ReadyItemStatus.PROCESSING
    # Narrow the base's worker handle back to the reading worker so sub-tabs can
    # read ReadingQueueWorker-specific attrs (e.g. the manga curation_document).
    worker_thread: ReadingQueueWorker | None

    def __init__(
        self,
        config: AnkiMinerConfig,
        processor: EpisodeProcessor | None = None,
        presenter: PresenterProtocol | None = None,
        parent: QWidget | None = None,
        stats_service: object | None = None,
    ) -> None:
        """Initialize the shared lifecycle state (see :class:`_QueueMiningTabBase`)."""
        super().__init__(config, processor, presenter, parent, stats_service)
        # Whole-run cards accumulator, tallied in _record_item_result and read by
        # _apply_terminal_bar_state (reset per run in _reset_run_state).
        self._run_cards_total: int = 0
        # Launch-banner strings, kept in the ReadingTab tr-context (see the module
        # i18n note in _queue_mining_tab_base). Built once at construction like
        # _ToolTabBase's _ToolTabStrings; the app installs the translator before
        # tabs are constructed, and reading has no runtime retranslate.
        self._run_strings = _QueueRunStrings(
            unavailable=QCoreApplication.translate("ReadingTab", "Mining unavailable — services not initialized."),
            run_starting=QCoreApplication.translate("ReadingTab", "%1 run starting — %2 items."),
            mine_label=QCoreApplication.translate("ReadingTab", "Mine"),
            retrying=QCoreApplication.translate("ReadingTab", "Attempt %1 of %2 · retrying in %3s"),
            # Extracted at the subclass's QT_TRANSLATE_NOOP, looked up here.
            task_title=QCoreApplication.translate("ReadingTab", self.TASK_TITLE) if self.TASK_TITLE else "",
        )

    # ------------------------------------------------------------------
    # Subclass hooks for the generic lifecycle
    # ------------------------------------------------------------------

    def _make_worker(
        self,
        items: list[Any],
        curation_callback: Callable[[list], list | None] | None,
        processor_factory: Callable[[], EpisodeProcessor] | None,
    ) -> SequentialQueueWorker[Any]:
        """Construct the reading queue worker (name resolves in this module for tests)."""
        return ReadingQueueWorker(
            processor=self._processor,
            config=self.config,
            items=items,
            curation_callback=curation_callback,
            processor_factory=processor_factory,
        )

    def _create_processor(self, presenter: PresenterProtocol) -> EpisodeProcessor:
        """Build a fresh processor (``create_episode_processor`` resolves here for tests)."""
        return create_episode_processor(
            self.config,
            presenter,
            stats_service=self._stats_service,  # type: ignore[arg-type]
        )

    def _reset_run_state(self, total: int) -> None:
        """Reset the whole-run cards accumulator."""
        self._run_cards_total = 0
        self._run_succeeded = 0
        self._run_failed_count = 0
        self._run_cancelled_count = 0

    # ------------------------------------------------------------------
    # Reading-specific helpers
    # ------------------------------------------------------------------

    def _detect_or_report(
        self,
        path: Path,
        detect_fn: Callable[[Path], list[ReadingSourceRef]] | None = None,
    ) -> list[ReadingSourceRef] | None:
        """Classify *path*, reporting any failure.

        Shared by the reading sub-tabs (manga folder / novel file / book
        folder): a ``SetupError`` carries a crafted, user-facing message and is
        surfaced verbatim; any other failure is logged and shown type-prefixed.
        Returns the detected refs on success, or ``None`` when detection failed
        (the caller then aborts the Mine without starting a run).

        ``detect_fn`` defaults to ``detector.detect``, resolved at call time —
        NOT as a def-time default, which would bind the function object at
        import and silently bypass the ``detector.detect`` patch seam the tab
        tests rely on. The novels tab passes ``detector.detect_book_folder``
        for its folder section.
        """
        try:
            return (detect_fn or detector.detect)(path)
        except SetupError as exc:
            self.log_widget.append_error(str(exc))
            return None
        except Exception as exc:  # noqa: BLE001 - surface any classify failure to the log
            logger.exception("Reading source detect failed for %s", path)
            self.log_widget.append_error(
                tr_format(QCoreApplication.translate("ReadingTab", "Could not process %1: %2"), path.name, exc)
            )
            return None

    def _record_item_outcome(self, result: object, error: object) -> MiningOutcome:
        """Classify and accumulate one worker item outcome.

        All four reading tabs forward their results from their own
        ``_on_item_finished`` -- they share no list-queue base -- but every one
        of them routes the outcome through here, so this is where the run
        receipt is fed for reading (D20).
        """
        self._record_receipt_result(result, error)
        outcome = MiningOutcome.FAILED if error is not None else classify_result(result)
        if outcome is MiningOutcome.SUCCESS:
            self._run_cards_total += int(getattr(result, "cards_created", 0) or 0)
            self._run_succeeded += 1
        elif outcome is MiningOutcome.CANCELLED:
            self._run_cancelled_count += 1
        else:
            self._run_failed_count += 1
        return outcome

    def _freeze_run_bar(self, widget) -> None:
        """Hold the run bar where it truly was when Cancel was pressed.

        Shared by the four reading sub-tabs' cancel handlers. Everything the run
        reports from here on concerns work it is abandoning, so the bar must
        stop advancing — and must not be zeroed at the end either, because how
        far the run actually got is exactly what the user stopped it to find out.
        """
        widget.freeze()
        widget.set_status(QCoreApplication.translate("ReadingTab", "Cancelling…"))
        # Told to the registry from the same place, so the pinned bar's clock
        # keeps running and the wait can name what it is waiting on (D22).
        self._publish_task_cancelling()

    def _apply_terminal_bar_state(self, widget) -> None:
        """Set the run's terminal bar state: cancel -> failed -> success.

        Reads only the per-run flags/accumulators seeded in :meth:`_launch_run`
        — never ``_run_items``, which is already cleared when the cleanup hook
        calls this. Also seals the run receipt, so the four reading tabs get
        their durable summary from the one hook all four already call.
        """
        cancelled = bool(getattr(self, "_cancel_requested", False) or self._run_cancelled_count)
        fatal = bool(getattr(self, "_run_failed", False))
        self._finish_receipt(cancelled=cancelled, fatal=fatal)
        outcome = classify_terminal_outcome(
            self._run_succeeded,
            self._run_failed_count,
            cancelled=cancelled,
            fatal=fatal,
        )
        if outcome is TerminalOutcome.CANCELLED:
            # No reset(): see _freeze_run_bar.
            widget.set_status(QCoreApplication.translate("ReadingTab", "Cancelled"))
        elif outcome in (TerminalOutcome.PARTIAL, TerminalOutcome.FAILED):
            widget.reset()
            widget.set_status(QCoreApplication.translate("ReadingTab", "Failed — see log"))
        else:
            widget.show_completion(
                tr_format(
                    QCoreApplication.translate("ReadingTab", "Complete — %1 cards created"),
                    self._run_cards_total,
                )
            )

    def _build_curation_context(
        self,
    ) -> tuple[CurationMediaContext | None, Callable[[str], list[tuple[str, str]]] | None]:
        """Table-only media context plus the offline-dictionary lookup pane.

        Reading has no player/subtitle media context (the ReadingQueueWorker
        publishes no ``_curation_video``/``_curation_subtitle``/``_curation_offset``,
        so touching those would AttributeError) — hence media stays ``None``. It
        does wire the definition pane: ``lookup_fn`` is sourced from the worker's
        ``curation_processor`` exactly like the video paths, so novels and
        subtitles show word meanings on row focus. The MANGA sub-tab overrides
        this to add a page-image context, keeping the same lookup_fn.
        """
        w = self.worker_thread
        proc = w.curation_processor if w is not None else None
        return None, self._lookup_fn_from_processor(proc)
