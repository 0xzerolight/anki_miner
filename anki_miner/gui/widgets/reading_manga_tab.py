"""Manga sub-tab of the Reading tab: one auto-detecting folder, no queue.

Pick a folder (or drop one), then Mine. The folder is classified by
``detector.detect``: a single-volume folder resolves to one volume, a series
folder to many. There is no queue — **Mine** runs whatever the folder resolves
to sequentially in one job (one ephemeral :class:`ReadingQueueItem` per volume)
through the shared
:class:`~anki_miner.gui.widgets._reading_mining_base._ReadingMiningTabBase`
lifecycle. Words are inspected
during Mine via the "Review words before mining" curation popup.

Progress uses two bars: the overall bar (vol N of M) appears only for a series
run of more than one volume; a single volume shows just the per-volume bar, so
it reads like the Novels tab.

The worker OWNS the item lifecycle (it sets ``status``/``cards_created``/
``error_message`` on each item, on the worker thread, before emitting its
signals), so this tab's signal slots are READ-ONLY on item state: they update
the progress bars and log outcomes, never write status/cards/error.

Drag-drop routes through the tab, not the file selector: the first dropped
folder / ``.mokuro`` / ``.cbz`` / ``.zip`` fills the selector; a novel drop
earns a cross-tab hint instead.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.resources.styles import FONT_SIZES, SPACING
from anki_miner.gui.utils.qt_helpers import urls_from_event
from anki_miner.gui.widgets._reading_mining_base import _ReadingMiningTabBase
from anki_miner.gui.widgets.base import field_label_width
from anki_miner.gui.widgets.dialogs.word_curation_dialog import CurationMediaContext
from anki_miner.gui.widgets.enhanced import FileSelector, ModernButton, SectionHeader
from anki_miner.gui.widgets.log_widget import LogWidget
from anki_miner.gui.widgets.progress_widget import ProgressWidget
from anki_miner.models.reading_queue import ReadingItemStatus, ReadingQueueItem
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from anki_miner.config import AnkiMinerConfig
    from anki_miner.interfaces.presenter import PresenterProtocol
    from anki_miner.orchestration import EpisodeProcessor

# Extensions accepted from a drag-drop (directories are always accepted). Manga
# sources fill the selector; novel drops earn a cross-tab hint.
_MANGA_EXTS = (".mokuro", ".cbz", ".zip")
_NOVEL_EXTS = (".epub", ".txt")


class ReadingMangaTab(_ReadingMiningTabBase):
    """Single auto-detecting folder manga mining sub-tab (no queue).

    Owns, via the base, at most one running
    :class:`~anki_miner.gui.workers.reading_queue_worker.ReadingQueueWorker`
    mining the volume(s) a folder resolves to. Button state is purely derived
    from the worker handle by :meth:`_recompute_buttons`: idle shows
    Mine, a run swaps it for Cancel.

    Manga curation shows page images (D8 amended): this tab overrides
    ``_build_curation_context`` to hand the dialog the in-flight volume's
    units (page image + mokuro block box per word) read off the parked
    worker's ``curation_document``. Novels stay table-only.
    """

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

        self._setup_ui()
        self._setup_drag_drop()
        # Route ALL drops through this tab's handler: the FileSelector sets any
        # dropped path unconditionally and its inner QLineEdit accepts URL drops
        # by default, so disable both so the drag manager delivers to the tab.
        self.volume_folder_selector.setAcceptDrops(False)
        self.volume_folder_selector.input.setAcceptDrops(False)
        self._recompute_buttons()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """Build the tab layout: one Manga card, checkbox, two bars, log."""
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        layout.addWidget(self._create_manga_card())

        # Issue #65: opt-in per-item word curation popup (default off).
        self.review_words_checkbox = QCheckBox(self.tr("Review words before mining"))
        self.review_words_checkbox.setChecked(False)
        self.review_words_checkbox.setToolTip(
            self.tr("Show the word-selection popup for each volume before creating cards.")
        )
        layout.addWidget(self.review_words_checkbox)

        # Single whole-run bar: per-volume sweeps are composed into it
        # ((volumes done + volume pct) / total), so a series run reads as one
        # continuous fill; the status label carries the active volume + stage.
        layout.addWidget(self._progress_header(self.tr("Progress")))
        self.overall_progress_widget = ProgressWidget()
        layout.addWidget(self.overall_progress_widget)

        # LogWidget (carries its own header + Copy/Clear actions).
        self.log_widget = LogWidget()
        layout.addWidget(self.log_widget, 1)

        container.setLayout(layout)
        scroll_area.setWidget(container)

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll_area)
        self.setLayout(main_layout)

    def _progress_header(self, text: str) -> QLabel:
        """Build a bold section-heading label for a progress bar."""
        header = QLabel(text)
        header.setObjectName("heading3")
        font = QFont()
        font.setPixelSize(FONT_SIZES.body)
        font.setWeight(QFont.Weight.Bold)
        header.setFont(font)
        return header

    def _create_manga_card(self) -> QFrame:
        """Manga card: folder selector + Mine / Cancel."""
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout()
        card_layout.setSpacing(SPACING.sm)
        card_layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        card_layout.addWidget(SectionHeader(title=self.tr("Manga")))

        self.volume_folder_selector = FileSelector(
            label=self.tr("Folder:"),
            file_mode=False,
            file_filter="",
            label_width=field_label_width("Folder:"),
        )
        self.volume_folder_selector.setToolTip(
            self.tr("A folder with one manga volume, or a series folder of many volumes.")
        )
        card_layout.addWidget(self.volume_folder_selector)

        button_row = QHBoxLayout()
        button_row.setSpacing(SPACING.sm)

        self.mine_button = ModernButton(self.tr("Mine"), variant="primary")
        self.mine_button.setToolTip(self.tr("Mine the selected folder's volume(s) into Anki cards."))
        self.mine_button.clicked.connect(self._on_mine_clicked)
        button_row.addWidget(self.mine_button)

        self.cancel_button = ModernButton(self.tr("Cancel"), variant="danger")
        self.cancel_button.setToolTip(self.tr("Cancel the active run."))
        self.cancel_button.clicked.connect(self._on_cancel_clicked)
        self.cancel_button.hide()
        button_row.addWidget(self.cancel_button)

        button_row.addStretch()
        card_layout.addLayout(button_row)

        card.setLayout(card_layout)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        return card

    # ------------------------------------------------------------------
    # Drag-and-drop (tab-level: manga sources fill the selector; novels hint)
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:
        """Accept a drag holding a directory or any reading file.

        Novels are accepted too so the drop can be delivered and answered with
        the cross-tab hint (they never fill the selector here).
        """
        if event is None:
            return
        for url in urls_from_event(event):
            local = Path(url.toLocalFile())
            suffix = local.suffix.lower()
            if local.is_dir() or suffix in _MANGA_EXTS or suffix in _NOVEL_EXTS:
                event.acceptProposedAction()
                return

    def dropEvent(self, event: QDropEvent | None) -> None:
        """Fill the selector from the first dropped folder/manga file; hint novels."""
        if event is None:
            return
        novel_seen = False
        source_set = False
        for url in urls_from_event(event):
            local = Path(url.toLocalFile())
            suffix = local.suffix.lower()
            if local.is_dir() or suffix in _MANGA_EXTS:
                if not source_set:
                    self.volume_folder_selector.set_path(str(local))
                    source_set = True
            elif suffix in _NOVEL_EXTS:
                novel_seen = True
        if novel_seen and not source_set:
            self.log_widget.append_info(self.tr("Novels are mined in the Novels tab."))
        event.acceptProposedAction()

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def _on_mine_clicked(self) -> None:
        """Mine — classify the folder and mine its volume(s) sequentially."""
        if self.worker_thread is not None:
            return
        refs = self._detected_refs()
        if refs is None:
            return

        items = [ReadingQueueItem(source=ref, title=ref.title, kind=ref.kind) for ref in refs]
        if self._launch_run(items, preview_mode=False):
            self._begin_progress(len(items))
            self._recompute_buttons()

    def _detected_refs(self) -> list | None:
        """Read the folder path and classify it, or ``None`` on empty/failure.

        Warns (and returns ``None``) when no folder is selected; otherwise
        delegates to the shared :meth:`_detect_or_report`, which surfaces any
        detector error verbatim in the log.
        """
        raw = self.volume_folder_selector.get_path().strip()
        if not raw:
            self.log_widget.append_warning(self.tr("Select a manga folder first."))
            return None
        return self._detect_or_report(Path(raw))

    def _begin_progress(self, total: int) -> None:
        """Reset the whole-run bar and seed the composition counters."""
        self._items_total = total
        self._current_item_title = ""
        self.overall_progress_widget.reset()
        self.overall_progress_widget.set_status(self.tr("Starting…"))

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
        self.overall_progress_widget.set_status(self.tr("Cancelling…"))

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
        phase 3 uses for card images). Falls back to the table-only
        ``(None, None)`` for novels-kind documents and image-less volumes.
        Imageless units are still included so unmatched pages show their
        page label in the placeholder. ``lookup_fn`` stays None — the
        definition pane remains out of scope for reading curation.
        """
        worker = self.worker_thread
        doc = worker.curation_document if worker is not None else None
        if doc is None or doc.kind != "manga" or not any(u.image_ref for u in doc.units):
            return None, None
        units = {u.index: u for u in doc.units}
        return CurationMediaContext(video_file=None, subtitle_entries=[], page_units=units), None

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

    def _on_item_progress(self, idx: int, label: str, pct: int) -> None:
        """Compose the volume's percent into the whole-run bar.

        ``idx`` doubles as the count of volumes already finished (items run
        sequentially), so the composed value is monotone across volume
        boundaries. ``pct < 0`` holds the bar with a status update.
        """
        title = getattr(self, "_current_item_title", "")
        status: str | None
        if label and title:
            status = f"{title} — {label}"
        elif label:
            status = label
        else:
            status = title or None
        if pct < 0:
            if status:
                self.overall_progress_widget.set_status(status)
            return
        self.overall_progress_widget.set_composed(idx, pct, len(self._run_items), status)

    def _on_item_finished(self, idx: int, result: object, error: object, attempts: int) -> None:
        """Log the outcome and advance the overall bar (series runs only).

        READ-ONLY: the worker has already recorded ``status``/``cards_created``/
        ``error_message`` on the item before emitting this signal, so this slot
        only reads them and never writes them.
        """
        item = self._item_at(idx)
        if item is None:
            return

        if error is None:
            cards = int(getattr(result, "cards_created", 0) or 0)
            self._record_item_result(result)
            self.log_widget.append_success(tr_format(self.tr("Mined %1: %2 cards."), item.title, cards))
            if self._presenter is not None:
                # Presenter forwarding is best-effort — the worker has already
                # recorded the result; a broken presenter slot shouldn't take
                # down the run.
                with contextlib.suppress(Exception):
                    self._presenter.show_processing_result(result)  # type: ignore[arg-type]
        else:
            self.log_widget.append_error(tr_format(self.tr("Failed %1: %2."), item.title, error))

        # Bar-only advance over items that reached a terminal state — keeps the
        # composed fill correct when a volume errors mid-sweep. Count-unit
        # writes (set_progress) are banned on the composition-driven widget.
        done = sum(1 for i in self._run_items if i.status in (ReadingItemStatus.COMPLETED, ReadingItemStatus.ERROR))
        self.overall_progress_widget.set_composed(done, 0, len(self._run_items))

    def _on_queue_finished(self) -> None:
        """Success-path summary log over the run snapshot. Cleanup is elsewhere.

        ``queue_finished`` is emitted from inside ``run()`` while ``_run_items``
        is still intact; ``QThread.finished`` fires later on every exit path and
        clears it. A single-volume run's outcome is already covered by
        ``_on_item_finished``, so only summarize a multi-volume run.
        """
        total = len(self._run_items)
        if total <= 1:
            return
        succeeded = sum(1 for i in self._run_items if i.status == ReadingItemStatus.COMPLETED)
        failed = sum(1 for i in self._run_items if i.status == ReadingItemStatus.ERROR)
        self.log_widget.append_info(tr_format(self.tr("Done: %1 succeeded, %2 failed."), succeeded, failed))

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
        """Refresh button state from the worker handle.

        Pure derived state: a live run hides Mine and shows Cancel; idle
        shows Mine and hides Cancel.
        """
        run_active = self.worker_thread is not None
        self.mine_button.setVisible(not run_active)
        self.mine_button.setEnabled(not run_active)
        self.cancel_button.setVisible(run_active)
