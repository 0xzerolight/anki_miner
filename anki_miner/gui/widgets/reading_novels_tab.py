"""Novels sub-tab of the Reading tab: one path field, one Mine (D7-B).

The field takes a single ``.epub``/``.txt`` book or a folder; Mine classifies
it. A folder is enumerated by ``detector.detect_book_folder`` (top-level books,
non-recursive) and every book is mined in turn — one ephemeral
:class:`ReadingQueueItem` per book through the shared
:class:`~anki_miner.gui.widgets._reading_mining_base._ReadingMiningTabBase`
lifecycle. Items are never stored; one hidden run-state widget covers the run
and the pinned bar shows it (D1); a failing book doesn't stop the rest.

The worker OWNS the item lifecycle (it sets ``status``/``cards_created``/
``error_message`` on the item, on the worker thread, before emitting its
signals), so this tab's signal slots are READ-ONLY on item state: they update
the progress bar and log the outcome, never write status/cards/error.

Drag-drop routes through the tab, not the file selector. The FileSelector's own
``dropEvent`` sets any dropped path unconditionally and its inner ``QLineEdit``
accepts URL drops by default, so the selector has ``setAcceptDrops(False)``
applied and every drop is delivered to this tab: the first ``.epub``/``.txt``
or directory fills the field (no disk I/O at drop time — a bookless folder
errors at Mine time); a manga- or subtitle-file drop earns the cross-tab hint
immediately.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import TYPE_CHECKING

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

from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.qt_helpers import urls_from_event
from anki_miner.gui.widgets._reading_mining_base import _ReadingMiningTabBase
from anki_miner.gui.widgets.base import (
    PageWidth,
    configure_card_layout,
    field_label_width,
)
from anki_miner.gui.widgets.enhanced import FileSelector, ModernButton
from anki_miner.gui.widgets.log_widget import LogWidget
from anki_miner.gui.widgets.progress_widget import ProgressWidget
from anki_miner.gui.widgets.reading_subtitles_tab import _SUBTITLE_EXTS
from anki_miner.models import MiningOutcome, result_error_text
from anki_miner.models.mining_queue import ReadyItemStatus
from anki_miner.models.reading_queue import ReadingQueueItem
from anki_miner.services.reading import aozora_source, detector
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from anki_miner.config import AnkiMinerConfig
    from anki_miner.interfaces.presenter import PresenterProtocol
    from anki_miner.models.reading import ReadingSourceRef
    from anki_miner.orchestration import EpisodeProcessor

# File-selector filter glob for the book-or-folder field. The human label ("Books")
# is tr()'d at call time; only the literal extension glob lives here.
_BOOK_FILTER_GLOB = "*.epub *.txt"

# Extensions this tab mines. A manga-kind (dirs always) or subtitle-kind drop
# earns a cross-tab hint instead of being mined here; the subtitle set is the
# Subtitles tab's own, imported so the hint covers every format it mines.
_NOVEL_EXTS = (".epub", ".txt")
_MANGA_EXTS = (".mokuro", ".cbz", ".zip")


class ReadingNovelsTab(_ReadingMiningTabBase):
    """Novel mining sub-tab: one book-or-folder field and one Mine (no queue UI, D7-B).

    Owns, via the base, at most one running
    :class:`~anki_miner.gui.workers.reading_queue_worker.ReadingQueueWorker`
    mining a list of ephemeral items — one for a single-book run, one per book
    for a folder run. Button state is purely derived from the worker handle by
    :meth:`_recompute_buttons`: idle shows Mine, a run swaps it for Cancel.

    Novels curation has no media context but shows the definition pane: the
    base's ``_build_curation_context`` returns ``(None, lookup_fn)`` from the
    worker's ``curation_processor`` — this tab does NOT override it (only the
    manga sub-tab does, to add a page-image context).
    """

    #: A label beside its control; a wider window buys gutters, not longer inputs.
    PAGE_WIDTH = PageWidth.PAGE

    #: Published so this screen's Cancel gets a live wait clock and the
    #: pinned bar gets a stage and a progress bar (D17, D22).
    TASK_ID = "queue.reading.novels"
    TASK_OWNER = CapabilityTarget("reading", "novels")
    #: Name this run carries away from this screen.
    TASK_TITLE = QT_TRANSLATE_NOOP("ReadingTab", "Novel mining")

    def __init__(
        self,
        config: AnkiMinerConfig,
        processor: EpisodeProcessor | None = None,
        presenter: PresenterProtocol | None = None,
        parent: QWidget | None = None,
        stats_service: object | None = None,
    ) -> None:
        """Initialize the novels sub-tab.

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
        self.book_selector.setAcceptDrops(False)
        self.book_selector.input.setAcceptDrops(False)
        self._recompute_buttons()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """Build the tab layout: one Novel card, checkbox, progress bar, log."""
        scroll_area = QScrollArea()

        container = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        layout.addWidget(self._create_novel_card())
        self._create_cancel_button()

        # Issue #65: opt-in per-item word curation popup (default off).
        self.review_words_checkbox = QCheckBox(self.tr("Review words before mining"))
        self._bind_review_words_checkbox()
        self.review_words_checkbox.setToolTip(self.tr("Show the word-selection popup before creating cards."))
        layout.addWidget(self.review_words_checkbox)

        # D1: the pinned bar is the one progress surface; this widget is the
        # run's hidden state holder and the receipt's anchor.
        self.progress_widget = ProgressWidget()
        self.progress_widget.hide()
        layout.addWidget(self.progress_widget)
        # The durable end state of this same card (D20).
        self._install_receipt(layout, self.progress_widget, item_noun=self.tr("books"))

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

    def _create_novel_card(self) -> QFrame:
        """Novel card: one book-or-folder field; Mine lives in the pinned bar."""
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout()
        configure_card_layout(card_layout)

        self.book_selector = FileSelector(
            label=self.tr("Book or folder:"),
            file_mode=True,
            allow_folder=True,
            file_filter=f"{self.tr('Books')} ({_BOOK_FILTER_GLOB})",
            label_width=field_label_width(self.tr("Book or folder:")),
            history_key="reading.novels.inputs",
        )
        self.book_selector.setToolTip(
            self.tr("An .epub or .txt book, or a folder of books; each book is mined separately.")
        )
        card_layout.addWidget(self.book_selector)

        # Mine is this screen's one run action, so it lives in the pinned bar
        # rather than in the card (D6). It mines a book or a whole folder (D7-B).
        self.mine_button = ModernButton(self.tr("Mine"), variant="primary")
        self.mine_button.setToolTip(self.tr("Mine the chosen book, or every book in the chosen folder."))
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
    # Drag-and-drop (tab-level: novels fill the selector; manga earns a hint)
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:
        """Accept a drag holding a book or another reading-kind source.

        Manga- and subtitle-kind sources are accepted too so the drop can be
        delivered and answered with the cross-tab hint (they never fill the
        selector here).
        """
        if event is None:
            return
        for url in urls_from_event(event):
            local = Path(url.toLocalFile())
            suffix = local.suffix.lower()
            if suffix in _NOVEL_EXTS or local.is_dir() or suffix in _MANGA_EXTS or suffix in _SUBTITLE_EXTS:
                event.acceptProposedAction()
                return

    def dropEvent(self, event: QDropEvent | None) -> None:
        """Fill the field from the first dropped book or directory; redirect other kinds.

        A dropped directory fills the field without any disk I/O
        (scanning inside a Qt drop handler risks an uncaught OSError and would
        duplicate ``detect_book_folder``); a bookless folder — e.g. a manga
        series dir — errors at Mine time with the cross-tab hint instead.
        """
        if event is None:
            return
        manga_seen = False
        subtitle_seen = False
        source_set = False
        for url in urls_from_event(event):
            local = Path(url.toLocalFile())
            suffix = local.suffix.lower()
            if suffix in _NOVEL_EXTS or local.is_dir():
                if not source_set:
                    self.book_selector.set_path(str(local))
                    source_set = True
            elif suffix in _MANGA_EXTS:
                manga_seen = True
            elif suffix in _SUBTITLE_EXTS:
                subtitle_seen = True
        if manga_seen:
            self.log_widget.append_info(self.tr("Manga is mined in the Manga tab."))
        if subtitle_seen:
            # A22: name a tab that exists.
            self.log_widget.append_info(self.tr("Subtitle files are mined in Reading → Subtitle Files."))
        event.acceptProposedAction()

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def _on_mine_clicked(self) -> None:
        """Mine — classify the field (a book or a folder), then mine it."""
        self._start_run()

    def _start_run(self) -> None:
        """Mine whatever the field holds: one book, or every book in a folder (D7-B).

        A book is classified by ``detector.detect``; a folder by
        ``detector.detect_book_folder`` (natural-sorted, non-recursive). A
        ``.txt`` over the loader's cap becomes one item per part
        (:meth:`_items_for`). A ``True`` launch swaps Mine for Cancel.
        """
        if self.worker_thread is not None:
            return
        # A fresh attempt supersedes the last complaint (after the reentrancy guard).
        self.clear_screen_issue()
        raw = self.book_selector.path_or_none()
        if raw is None:
            self._report_refusal(self.tr("Choose a book or a folder of books first."))
            return
        path = Path(raw)
        if path.is_dir():
            refs = self._detect_or_report(path, detect_fn=detector.detect_book_folder)
            if refs is None:
                return
            items = self._items_for(refs)
        elif path.is_file() and path.suffix.lower() in _NOVEL_EXTS:
            refs = self._detect_or_report(path)
            if refs is None:
                return
            items = self._items_for(refs[:1])
        else:
            self._report_refusal(self.tr("Choose an .epub or .txt book, or a folder of books."), details=raw)
            return
        if self._launch_run(items):
            self._begin_run()

    @staticmethod
    def _items_for(refs: list[ReadingSourceRef]) -> list[ReadingQueueItem]:
        """One ephemeral item per book, or per part of a ``.txt`` over the loader's cap.

        A part is its own item so a huge file mines with the folder run's
        machinery: a bounded load per item, Cancel between items, and one
        failing part not stopping the rest.
        """
        return [
            ReadingQueueItem(source=ref, title=ref.title, kind=ref.kind) for ref in aozora_source.split_oversize(refs)
        ]

    def _begin_run(self) -> None:
        """Reset the progress bar and swap to the running button state."""
        self.progress_widget.reset()
        self.progress_widget.set_status(self.tr("Starting…"))
        self._recompute_buttons()

    def _cancel_published_task(self) -> None:
        """Route a registry cancel request into this screen's own Cancel."""
        self._on_cancel_clicked()

    def _on_cancel_clicked(self) -> None:
        """Cancel the active run."""
        self._cancel_requested = True
        # Release any open curation dialog first so the blocked worker resumes
        # instead of hanging on the curation gate (Issue #65).
        self._cancel_active_curation_dialog()
        worker = self.worker_thread
        if worker is None:
            return
        worker.cancel()
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText(self.tr("Cancelling…"))
        self._freeze_run_bar(self.progress_widget)

    # ------------------------------------------------------------------
    # Per-item signal slots (READ-ONLY on item state — the worker owns it)
    # ------------------------------------------------------------------

    def _on_item_started(self, idx: int) -> None:
        """Seed the progress bar with the started book's title.

        READ-ONLY: the worker has already set ``status`` to PROCESSING before
        emitting this signal, so this only reflects current state.

        Folder runs put the "Book N/M" prefix in ``_current_item_title`` — not
        just the status line — because ``_on_item_progress`` recomposes the
        status from ``_current_item_title`` on every tick, so a status-only
        prefix would vanish on the first progress signal.

        N/M counts books, not items: the parts of one over-cap ``.txt`` share
        a path, and their titles already say "(i/n)".
        """
        item = self._item_at(idx)
        if item is None:
            return
        books = list(dict.fromkeys(i.source.path for i in self._run_items))
        if len(books) > 1:
            self._current_item_title = tr_format(
                self.tr("Book %1/%2: %3"), books.index(item.source.path) + 1, len(books), item.title
            )
            self.progress_widget.set_status(self._current_item_title)
            self._publish_reading_status(self._current_item_title)
        else:
            self._current_item_title = item.title
            # Status only — the composed bar never resets between items.
            status = tr_format(self.tr("Mining: %1"), item.title)
            self.progress_widget.set_status(status)
            self._publish_reading_status(status)

    def _on_item_progress(self, idx: int, label: str) -> None:
        """Say what the book is doing. The bar counts finished books only."""
        title = getattr(self, "_current_item_title", "")
        status: str | None
        if label and title:
            status = f"{title} — {label}"
        elif label:
            status = label
        else:
            status = title or None
        if status:
            self.progress_widget.set_status(status)
            self._publish_reading_status(status)

    def _on_item_finished(self, idx: int, result: object, error: object, attempts: int) -> None:
        """Log the outcome and forward a success result to the presenter.

        READ-ONLY: the worker has already recorded ``status``/``cards_created``/
        ``error_message`` on the item before emitting this signal, so this slot
        only reads them and never writes them.
        """
        item = self._item_at(idx)
        if item is None:
            return

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
        # composed fill correct when a book errors mid-run. Count-unit writes
        # (set_progress) are banned on the composition-driven widget.
        done = sum(1 for i in self._run_items if i.status in (ReadyItemStatus.COMPLETED, ReadyItemStatus.ERROR))
        self.progress_widget.set_composed(done, len(self._run_items))
        self._publish_reading_done(done)

    def _on_queue_finished(self) -> None:
        """Run summary for folder runs. Cleanup is elsewhere.

        ``queue_finished`` is emitted from inside ``run()`` while ``_run_items``
        is still intact; ``QThread.finished`` fires later on every exit path and
        clears it. A single-book run's outcome is already covered by
        ``_on_item_finished``, so only summarize a multi-book run. The lead is
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

        Restores the Cancel button, resets the progress bar, and recomputes
        button state. Runs on every run-exit path (success, cancel, exception).
        """
        self.cancel_button.setText(self.tr("Cancel"))
        self.cancel_button.setEnabled(True)
        self._apply_terminal_bar_state(self.progress_widget)
        self._recompute_buttons()

    # ------------------------------------------------------------------
    # Button recomputation
    # ------------------------------------------------------------------

    def _recompute_buttons(self) -> None:
        """Refresh button state from the worker handle.

        Pure derived state: a live run hides Mine and shows Cancel; idle
        shows the one Mine (A04: always enabled) and hides Cancel.
        """
        run_active = self.worker_thread is not None
        self.mine_button.setVisible(not run_active)
        self.mine_button.setEnabled(not run_active)
        self.cancel_button.setVisible(run_active)
