"""Manga sub-tab of the Reading tab: one path field, one Mine (D7-B).

The field takes a single ``.mokuro``/``.cbz``/``.zip`` volume or a folder (one
volume, or a series folder of many), and Mine classifies whatever it holds
through ``detector.detect``. A ``.cbz`` resolves through its sibling
``.mokuro`` or an embedded ``.mokuro`` member. There is no queue — each Mine
runs its volumes sequentially in one job (one ephemeral
:class:`ReadingQueueItem` per volume) through the shared
:class:`~anki_miner.gui.widgets._reading_mining_base._ReadingMiningTabBase`
lifecycle. Words are inspected during Mine via the "Review words before
mining" curation popup.

One composed whole-run bar: the overall bar (vol N of M) status appears only
for a run of more than one volume.

The worker OWNS the item lifecycle (it sets ``status``/``cards_created``/
``error_message`` on each item, on the worker thread, before emitting its
signals), so this tab's signal slots are READ-ONLY on item state: they update
the progress bars and log outcomes, never write status/cards/error.

Drag-drop routes through the tab, not the file selector: the first dropped
volume or folder fills the field; a novel or subtitle drop earns a hint naming
the right sub-tab.
"""

from __future__ import annotations

import contextlib
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, cast

from PyQt6.QtCore import QT_TRANSLATE_NOOP
from PyQt6.QtGui import QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from anki_miner.exceptions import SetupError
from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.qt_helpers import urls_from_event
from anki_miner.gui.utils.run_off_thread import run_off_thread
from anki_miner.gui.widgets._reading_mining_base import _ReadingMiningTabBase
from anki_miner.gui.widgets.base import (
    PageWidth,
    ScreenIssue,
    configure_card_layout,
    field_label_width,
)
from anki_miner.gui.widgets.dialogs.word_curation_dialog import CurationMediaContext
from anki_miner.gui.widgets.enhanced import FileSelector, ModernButton
from anki_miner.gui.widgets.log_widget import LogWidget
from anki_miner.gui.widgets.progress_widget import ProgressWidget
from anki_miner.gui.widgets.reading_subtitles_tab import _SUBTITLE_EXTS
from anki_miner.languages.registry import config_language, get_profile
from anki_miner.models import MiningOutcome, result_error_text
from anki_miner.models.mining_queue import ReadyItemStatus
from anki_miner.models.reading_queue import ReadingQueueItem
from anki_miner.services.reading import detector
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from anki_miner.config import AnkiMinerConfig
    from anki_miner.gui.workers.base_worker import SingleCallWorker
    from anki_miner.interfaces.presenter import PresenterProtocol
    from anki_miner.models.reading import ReadingSourceRef
    from anki_miner.orchestration import EpisodeProcessor

# Extensions accepted from a drag-drop (directories are always accepted). A
# manga file or a directory fills the one field; novel/subtitle drops earn a
# cross-tab hint. The subtitle set is the Subtitles tab's
# own, imported so the hint covers every format it mines.
_MANGA_EXTS = (".mokuro", ".cbz", ".zip")
_NOVEL_EXTS = (".epub", ".txt")

# File-selector filter glob for the volume-or-folder field. The human label
# ("Manga") is tr()'d at call time; only the literal extension glob lives here.
_MANGA_FILTER_GLOB = "*.mokuro *.cbz *.zip"

#: The mining-language capability that offers Utilities → Manga OCR. WS4's E17
#: adds it to the Japanese profile (mokuro's OCR model is Japanese-only), so
#: the hand-off is offered exactly where the tool is.
_MANGA_OCR_CAPABILITY = "manga_ocr"

#: Lower-cased text both "no OCR data" refusals of ``detector`` contain: the
#: archive one ("No .mokuro data found for …") and the folder one ("… no
#: .mokuro volumes …").
_NO_OCR_MARKER = "no .mokuro"


def _queue_item_title(ref: ReadingSourceRef) -> str:
    """Label a queue item for progress/log lines.

    For a mokuro volume ``ref.title`` is the SERIES and ``ref.volume`` the
    actual volume; dropping the volume made every volume's progress/log line
    read "SeriesName" (can't tell which volume failed). Append the volume so it
    matches the per-card source label ``process_reading`` builds
    (``f"{series} — {episode}"``). Non-mokuro refs carry no volume and are left
    unchanged.
    """
    if ref.kind == "mokuro" and ref.volume:
        return f"{ref.title} — {ref.volume}"
    return ref.title


class ReadingMangaTab(_ReadingMiningTabBase):
    """Manga mining sub-tab: one path field and one Mine (no queue, D7-B).

    Owns, via the base, at most one running
    :class:`~anki_miner.gui.workers.reading_queue_worker.ReadingQueueWorker`
    mining the volume(s) a pick resolves to. Button state is derived from the
    detection and worker handles by :meth:`_recompute_buttons`: pending
    detection disables Mine; a run swaps it for Cancel.

    Manga curation shows page images (D8 amended): this tab overrides
    ``_build_curation_context`` to hand the dialog the in-flight volume's
    units (page image + mokuro block box per word) read off the parked
    worker's ``curation_document``, alongside the definition-pane lookup_fn
    that the reading base already supplies.
    """

    #: A label beside its control; a wider window buys gutters, not longer inputs.
    PAGE_WIDTH = PageWidth.PAGE

    #: Published so this screen's Cancel gets a live wait clock and the
    #: pinned bar gets a stage and a progress bar (D17, D22).
    TASK_ID = "queue.reading.manga"
    TASK_OWNER = CapabilityTarget("reading", "manga")
    #: Name this run carries away from this screen.
    TASK_TITLE = QT_TRANSLATE_NOOP("ReadingTab", "Manga mining")

    def __init__(
        self,
        config: AnkiMinerConfig,
        processor: EpisodeProcessor | None = None,
        presenter: PresenterProtocol | None = None,
        parent: QWidget | None = None,
        stats_service: object | None = None,
    ) -> None:
        """Initialize the manga sub-tab.

        Args:
            config: Frozen application configuration.
            processor: Episode processor (reused across runs within this tab).
                May be ``None`` so the tab can be constructed before the
                dictionary chain has loaded; the first run builds one lazily.
            presenter: Optional presenter for routing results.
            parent: Optional parent widget.
            stats_service: Optional ``StatsService`` reused across lazy
                processor rebuilds so reading mining sessions land in analytics.
        """
        super().__init__(config, processor, presenter, parent, stats_service)
        self._detection_pending = False
        self._detection_worker: SingleCallWorker | None = None
        self._detection_generation = 0
        self._detection_shutdown = False
        # The path the latest detection was started for (the no-OCR pointer reads it).
        self._detecting_path: Path | None = None

        self._setup_ui()
        self._setup_drag_drop()
        # Route ALL drops through this tab's handler: the FileSelector sets any
        # dropped path unconditionally and its inner QLineEdit accepts URL drops
        # by default, so disable both so the drag manager delivers to the tab.
        self.volume_file_selector.setAcceptDrops(False)
        self.volume_file_selector.input.setAcceptDrops(False)
        self._recompute_buttons()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """Build the tab layout: the source card, checkbox, hidden bar, log."""
        scroll_area = QScrollArea()

        container = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        layout.addWidget(self._create_source_card())
        self._create_cancel_button()

        # Issue #65: opt-in per-item word curation popup (default off).
        self.review_words_checkbox = QCheckBox(self.tr("Review words before mining"))
        self._bind_review_words_checkbox()
        self.review_words_checkbox.setToolTip(
            self.tr("Show the word-selection popup for each volume before creating cards.")
        )
        layout.addWidget(self.review_words_checkbox)

        # D1: the pinned bar is the one progress surface; this widget is the
        # run's hidden state holder and the receipt's anchor.
        self.overall_progress_widget = ProgressWidget()
        self.overall_progress_widget.hide()
        layout.addWidget(self.overall_progress_widget)
        # The durable end state of this same card (D20).
        self._install_receipt(layout, self.overall_progress_widget, item_noun=self.tr("volumes"))

        # LogWidget: own header + Copy/Clear actions; install_workflow_shell moves it into the Activity drawer (D6).
        self.log_widget = LogWidget(source=self.TASK_ID or type(self).__name__)

        container.setLayout(layout)

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        self._install_action_bar(
            main_layout,
            scroll_area,
            container,
            self.PAGE_WIDTH,
            primary=self.mine_button,
            secondary=(self.cancel_button,),
            log=self.log_widget,
        )
        self.setLayout(main_layout)

    def _create_source_card(self) -> QFrame:
        """The one card: a volume-or-folder field (D7-B). No heading: the sub-tab names it (A20)."""
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout()
        configure_card_layout(card_layout)

        self.volume_file_selector = FileSelector(
            label=self.tr("Volume or folder:"),
            file_mode=True,
            allow_folder=True,
            file_filter=f"{self.tr('Manga')} ({_MANGA_FILTER_GLOB})",
            label_width=field_label_width(self.tr("Volume or folder:")),
            history_key="reading.manga.inputs",
        )
        self.volume_file_selector.setToolTip(
            self.tr(
                "A .mokuro volume, a .cbz/.zip archive with its .mokuro beside or inside it, "
                "or a folder of volumes. No extraction needed."
            )
        )
        card_layout.addWidget(self.volume_file_selector)

        # Mine is this screen's one run action, so it lives in the pinned bar (D6).
        self.mine_button = ModernButton(self.tr("Mine"), variant="primary")
        self.mine_button.setToolTip(self.tr("Mine the chosen volume, or every volume in the chosen folder."))
        self.mine_button.clicked.connect(self._on_mine_clicked)

        card.setLayout(card_layout)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        return card

    def _create_cancel_button(self) -> None:
        """Build the one Cancel that serves either run kind.

        It no longer needs a row of its own: it lives in the pinned bar (D6),
        which is where a control that must stay reachable during a long run
        belongs.
        """
        self.cancel_button = ModernButton(self.tr("Cancel"), variant="secondary")
        self.cancel_button.setToolTip(self.tr("Cancel the active run."))
        self.cancel_button.clicked.connect(self._on_cancel_clicked)
        self.cancel_button.hide()

    # ------------------------------------------------------------------
    # Drag-and-drop (tab-level: manga sources fill the selector; novels hint)
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:
        """Accept a drag holding a directory or any reading file.

        Novels and subtitles are accepted too so the drop can be delivered and
        answered with the cross-tab hint (they never fill the selector here).
        """
        if event is None:
            return
        for url in urls_from_event(event):
            local = Path(url.toLocalFile())
            suffix = local.suffix.lower()
            if local.is_dir() or suffix in _MANGA_EXTS or suffix in _NOVEL_EXTS or suffix in _SUBTITLE_EXTS:
                event.acceptProposedAction()
                return

    def dropEvent(self, event: QDropEvent | None) -> None:
        """Fill the field from the first dropped volume or folder; hint other kinds."""
        if event is None:
            return
        novel_seen = False
        subtitle_seen = False
        source_set = False
        for url in urls_from_event(event):
            local = Path(url.toLocalFile())
            suffix = local.suffix.lower()
            if suffix in _MANGA_EXTS or local.is_dir():
                if not source_set:
                    self.volume_file_selector.set_path(str(local))
                    source_set = True
            elif suffix in _NOVEL_EXTS:
                novel_seen = True
            elif suffix in _SUBTITLE_EXTS:
                subtitle_seen = True
        if novel_seen and not source_set:
            self.log_widget.append_info(self.tr("Novels are mined in the Novels tab."))
        if subtitle_seen and not source_set:
            # A22: name a tab that exists.
            self.log_widget.append_info(self.tr("Subtitle files are mined in Reading → Subtitle Files."))
        event.acceptProposedAction()

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def _on_mine_clicked(self) -> None:
        """Mine: classify whatever the field holds, a volume or a folder (D7-B)."""
        if self.worker_thread is not None or self._detection_pending:
            return
        # A fresh attempt supersedes the last complaint (after the reentrancy guard).
        self.clear_screen_issue()
        raw = self.volume_file_selector.path_or_none()
        if raw is None:
            self._report_refusal(self.tr("Choose a manga volume or folder first."))
            return
        path = Path(raw)
        if path.is_dir() or (path.is_file() and path.suffix.lower() in _MANGA_EXTS):
            self._detect_and_launch(path)
            return
        self._report_refusal(self.tr("Choose a .mokuro, .cbz or .zip volume, or a manga folder."), details=raw)

    def _detect_and_launch(self, path: Path) -> None:
        """Classify ``path`` off-thread, then launch survivors on the GUI thread."""
        if self._detection_shutdown:
            return
        self._detection_generation += 1
        generation = self._detection_generation
        self._detection_pending = True
        self._detecting_path = path
        self._recompute_buttons()

        def _detect() -> tuple[list[ReadingSourceRef], list[tuple[Path, str]]]:
            diagnostics: list[tuple[Path, str]] = []
            try:
                refs = detector.detect(path, diagnostics=diagnostics)
            except SetupError:
                raise
            except Exception as exc:
                message = tr_format(self.tr("Could not process %1: %2"), path.name, exc)
                raise RuntimeError(message) from exc
            return refs, diagnostics

        self._detection_worker = run_off_thread(
            self,
            _detect,
            lambda result: self._on_detection_done(generation, result),
            lambda message: self._on_detection_error(generation, message),
            on_finished=lambda: self._on_detection_finished(generation),
        )

    def _is_current_detection(self, generation: int) -> bool:
        """Return whether a detection callback still owns this tab."""
        return not self._detection_shutdown and generation == self._detection_generation

    def _on_detection_done(self, generation: int, result: object) -> None:
        """Report skipped archives once, then launch every valid volume."""
        if not self._is_current_detection(generation):
            return
        refs, diagnostics = cast(tuple[list, list[tuple[Path, str]]], result)
        if diagnostics:
            details = "; ".join(f"{path.name}: {reason}" for path, reason in diagnostics)
            self.log_widget.append_warning(tr_format(self.tr("Skipped volumes: %1"), details))
        self._launch_detected(refs)

    def _on_detection_error(self, generation: int, message: str) -> None:
        """Report a detection failure on the screen, only while its generation is current (A04, A16)."""
        if self._is_current_detection(generation):
            self._report_detection_failure(self._detecting_path, message)

    def _report_detection_failure(self, path: Path | None, message: str) -> None:
        """Say why the pick can't be mined; point a text-less manga at Manga OCR (A16).

        A plain ``.cbz`` or a folder of page images has no OCR text layer, which
        is a dead end on this screen but not in the app: Manga OCR makes one.
        The hand-off is offered only where the tool is (``manga_ocr``), and the
        sentence names the folder it will fill in: the picked folder, or the
        folder a picked archive sits in.
        """
        if path is None or _NO_OCR_MARKER not in message.lower():
            self._report_unmineable(message)
            return
        self.log_widget.append_error(message)
        folder = path if path.is_dir() else path.parent
        if not self._manga_ocr_offered():
            self.show_screen_issue(
                ScreenIssue(summary=self.tr("This manga has no text layer yet, so it can't be mined."), details=message)
            )
            return
        summary = (
            self.tr("This manga has no text layer yet. Manga OCR can make one for this folder.")
            if path.is_dir()
            else self.tr("This manga has no text layer yet. Manga OCR can make one for the folder this file is in.")
        )
        self.show_screen_issue(
            ScreenIssue(
                summary=summary,
                details=message,
                action_id="tools.mokuro",
                action_text=self.tr("Open Manga OCR"),
            ),
            action=partial(self._open_manga_ocr, folder),
        )

    def _manga_ocr_offered(self) -> bool:
        """Whether this mining language has Utilities → Manga OCR (E17's gate)."""
        return _MANGA_OCR_CAPABILITY in get_profile(config_language(self.config)).capabilities

    def _open_manga_ocr(self, folder: Path) -> None:
        """Show Utilities → Manga OCR with ``folder`` already in its field (A16)."""
        from anki_miner.gui.widgets.mokuro_tab import MokuroTab

        window = self.window()
        reveal = getattr(window, "reveal_capability", None)
        if callable(reveal):
            reveal(CapabilityTarget("subtitles", "mokuro"))
        tool = window.findChild(MokuroTab) if window is not None else None
        if tool is not None:
            tool.folder_selector.set_path(str(folder))

    def _on_detection_finished(self, generation: int) -> None:
        """Restore start actions after detection succeeds or fails."""
        if not self._is_current_detection(generation):
            return
        self._detection_worker = None
        self._detection_pending = False
        self._recompute_buttons()

    def _launch_detected(self, refs: list | None) -> None:
        """Launch one ephemeral item per detected volume (shared by both cards)."""
        if refs is None:
            return
        items = [ReadingQueueItem(source=ref, title=_queue_item_title(ref), kind=ref.kind) for ref in refs]
        if self._launch_run(items):
            self._begin_progress(len(items))
            self._recompute_buttons()

    def _begin_progress(self, total: int) -> None:
        """Reset the whole-run bar and seed the composition counters."""
        self._items_total = total
        self._current_item_title = ""
        self.overall_progress_widget.reset()
        self.overall_progress_widget.set_status(self.tr("Starting…"))

    def _cancel_published_task(self) -> None:
        """Route a registry cancel request into this screen's own Cancel."""
        self._on_cancel_clicked()

    def _on_cancel_clicked(self) -> None:
        """Cancel the active run."""
        self._cancel_requested = True
        # Release any open curation dialog first so the blocked worker resumes
        # instead of hanging on _curation_event (Issue #65).
        self._cancel_active_curation_dialog()
        worker = self.worker_thread
        if worker is None:
            return
        worker.cancel()
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText(self.tr("Cancelling…"))
        self._freeze_run_bar(self.overall_progress_widget)

    def shutdown(self) -> None:
        """Invalidate pending detection before stopping the mining worker."""
        self._detection_shutdown = True
        self._detection_generation = getattr(self, "_detection_generation", 0) + 1
        detection_worker = getattr(self, "_detection_worker", None)
        self._detection_worker = None
        self._detection_pending = False
        if detection_worker is not None:
            with contextlib.suppress(RuntimeError):
                detection_worker.cancel()
        super().shutdown()

    # ------------------------------------------------------------------
    # Curation context (D8 amended: manga shows page images)
    # ------------------------------------------------------------------

    def _build_curation_context(self):
        """Page-image curation context for the in-flight manga volume.

        Runs off the GUI thread (run_off_thread in the base). Reads the
        worker's published ``curation_document`` — stable because the worker
        is parked in the curation Event wait for the whole build — and hands
        the dialog a plain ``{unit.index: ReadingUnit}`` map (the dialog
        resolves a word's unit via ``int(word.start_time)``, the same mapping
        phase 3 uses for card images). Falls back to a media-less context for
        novels-kind documents and image-less volumes. Imageless units are
        still included so unmatched pages show their page label in the
        placeholder. Either way the definition-pane ``lookup_fn`` is wired
        from the worker's ``curation_processor`` (same as novels/subtitles).
        """
        worker = self.worker_thread
        proc = worker.curation_processor if worker is not None else None
        lookup = self._lookup_fn_from_processor(proc)
        doc = worker.curation_document if worker is not None else None
        if doc is None or doc.kind != "manga" or not any(u.image_ref for u in doc.units):
            return None, lookup
        units = {u.index: u for u in doc.units}
        return CurationMediaContext(video_file=None, subtitle_entries=[], page_units=units), lookup

    # ------------------------------------------------------------------
    # Per-item signal slots (READ-ONLY on item state — the worker owns it)
    # ------------------------------------------------------------------

    def _on_item_started(self, idx: int) -> None:
        """Seed the per-volume bar with the started volume's title.

        READ-ONLY: the worker has already set ``status`` to PROCESSING before
        emitting this signal, so this only reflects current state — never write
        it here (a late-delivered start must not clobber a status the worker has
        since advanced to COMPLETED/ERROR).
        """
        item = self._item_at(idx)
        if item is None:
            return
        total = len(self._run_items)
        if total > 1:
            self._current_item_title = tr_format(self.tr("Volume %1/%2: %3"), idx + 1, total, item.title)
        else:
            self._current_item_title = item.title
        # Status only — the composed whole-run bar never resets between volumes.
        self.overall_progress_widget.set_status(self._current_item_title)
        self._publish_reading_status(self._current_item_title)

    def _on_item_progress(self, idx: int, label: str) -> None:
        """Say what the volume is doing. The bar counts finished volumes only."""
        title = getattr(self, "_current_item_title", "")
        status: str | None
        if label and title:
            status = f"{title} — {label}"
        elif label:
            status = label
        else:
            status = title or None
        if status:
            self.overall_progress_widget.set_status(status)
            self._publish_reading_status(status)

    def _on_item_finished(self, idx: int, result: object, error: object, attempts: int) -> None:
        """Log the outcome and advance the overall bar (series runs only).

        READ-ONLY: the worker has already recorded ``status``/``cards_created``/
        ``error_message`` on the item before emitting this signal, so this slot
        only reads them and never writes them.
        """
        item = self._item_at(idx)
        if item is None:
            return

        # A worker exception arrives as a non-None error; a non-raising return
        # (success, failure, or a cancel mid-mine) arrives as error=None with the
        # verdict inside the result. Classify both so a cancelled volume isn't
        # logged as a green "Mined 0 cards." success.
        outcome = self._record_item_outcome(result, error)
        if outcome is MiningOutcome.SUCCESS:
            cards = int(getattr(result, "cards_created", 0) or 0)
            self.log_widget.append_success(tr_format(self.tr("Mined %1: %2 cards."), item.title, cards))
            if self._presenter is not None:
                # Presenter forwarding is best-effort — the worker has already
                # recorded the result; a broken presenter slot shouldn't take
                # down the run.
                with contextlib.suppress(Exception):
                    self._presenter.show_processing_result(result)  # type: ignore[arg-type]
        elif outcome is MiningOutcome.CANCELLED:
            self.log_widget.append_info(tr_format(self.tr("Cancelled %1."), item.title))
        else:
            message = str(error) if error is not None else result_error_text(result)
            self.log_widget.append_error(tr_format(self.tr("Failed %1: %2."), item.title, message))

        # Bar-only advance over items that reached a terminal state — keeps the
        # composed fill correct when a volume errors mid-sweep. Count-unit
        # writes (set_progress) are banned on the composition-driven widget.
        done = sum(1 for i in self._run_items if i.status in (ReadyItemStatus.COMPLETED, ReadyItemStatus.ERROR))
        self.overall_progress_widget.set_composed(done, len(self._run_items))
        self._publish_reading_done(done)

    def _on_queue_finished(self) -> None:
        """Run summary log over the run snapshot. Cleanup is elsewhere.

        ``queue_finished`` is emitted from inside ``run()`` while ``_run_items``
        is still intact; ``QThread.finished`` fires later on every exit path and
        clears it. A single-volume run's outcome is already covered by
        ``_on_item_finished``, so only summarize a multi-volume run. The lead is
        chosen by ``_log_queue_summary``, which swaps it on a cancelled run.
        """
        total = len(self._run_items)
        if total <= 1:
            return
        succeeded = sum(1 for i in self._run_items if i.status == ReadyItemStatus.COMPLETED)
        failed = sum(1 for i in self._run_items if i.status == ReadyItemStatus.ERROR)
        self._log_queue_summary(self.tr("Done: %1 succeeded, %2 failed."), succeeded, failed)

    def _after_run_cleanup(self) -> None:
        """Per-tab UI recovery after a run ends (called from the base cleanup slot).

        Restores the Cancel button, resets + hides the overall bar and resets the
        per-volume bar, and recomputes button state. Runs on every run-exit path
        (success, cancel, exception).
        """
        self.cancel_button.setText(self.tr("Cancel"))
        self.cancel_button.setEnabled(True)
        self._apply_terminal_bar_state(self.overall_progress_widget)
        self._recompute_buttons()

    # ------------------------------------------------------------------
    # Button recomputation
    # ------------------------------------------------------------------

    def _recompute_buttons(self) -> None:
        """Refresh button state from the detection and worker handles.

        Pending detection disables Mine; a live run hides Mine and shows
        Cancel; idle shows Mine and hides Cancel.
        """
        run_active = self.worker_thread is not None
        self.mine_button.setVisible(not run_active)
        # A04: always offered while idle; only a detection already running holds it.
        self.mine_button.setEnabled(not run_active and not self._detection_pending)
        self.cancel_button.setVisible(run_active)
