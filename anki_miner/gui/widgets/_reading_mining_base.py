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
from anki_miner.models.reading_queue import ReadingItemStatus
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
    # Enable the promoted stranded-PROCESSING recovery sweep for reading too.
    _status_ready = ReadingItemStatus.READY
    _status_processing = ReadingItemStatus.PROCESSING
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

    # ------------------------------------------------------------------
    # Reading-specific helpers
    # ------------------------------------------------------------------

    def _detect_or_report(self, path: Path) -> list[ReadingSourceRef] | None:
        """Classify *path* with ``detector.detect``, reporting any failure.

        Shared by both reading sub-tabs (manga folder / novel file): a
        ``SetupError`` carries a crafted, user-facing message and is surfaced
        verbatim; any other failure is logged and shown type-prefixed. Returns
        the detected refs on success, or ``None`` when detection failed (the
        caller then aborts the Mine without starting a run).
        """
        try:
            return detector.detect(path)
        except SetupError as exc:
            self.log_widget.append_error(str(exc))
            return None
        except Exception as exc:  # noqa: BLE001 - surface any classify failure to the log
            logger.exception("Reading source detect failed for %s", path)
            self.log_widget.append_error(
                tr_format(QCoreApplication.translate("ReadingTab", "Could not process %1: %2"), path.name, exc)
            )
            return None

    def _record_item_result(self, result: object) -> None:
        """Accumulate per-run summary counts from a successful item result."""
        self._run_cards_total += int(getattr(result, "cards_created", 0) or 0)

    def _apply_terminal_bar_state(self, widget) -> None:
        """Set the run's terminal bar state: cancel -> failed -> success.

        Reads only the per-run flags/accumulators seeded in :meth:`_launch_run`
        — never ``_run_items``, which is already cleared when the cleanup hook
        calls this.
        """
        if getattr(self, "_cancel_requested", False):
            widget.reset()
            widget.set_status(QCoreApplication.translate("ReadingTab", "Cancelled"))
        elif getattr(self, "_run_failed", False):
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
