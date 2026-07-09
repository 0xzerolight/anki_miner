"""Novels sub-tab of the Reading tab: single-file Preview / Mine (no queue).

A novel is one ``.epub``/``.txt`` book: pick it in the file selector (or drop
it), then Preview or Mine. Unlike the manga sub-tab there is no batch queue —
every run mines exactly one ephemeral :class:`ReadingQueueItem` through the
shared :class:`~anki_miner.gui.widgets._reading_mining_base._ReadingMiningTabBase`
lifecycle (a one-item list handed to the queue worker). One progress bar, no
rows, no Add/Clear buttons.

The worker OWNS the item lifecycle (it sets ``status``/``cards_created``/
``error_message`` on the item, on the worker thread, before emitting its
signals), so this tab's signal slots are READ-ONLY on item state: they update
the progress bar and log the outcome, never write status/cards/error.

Drag-drop routes through the tab, not the file selector. The FileSelector's own
``dropEvent`` sets any dropped path unconditionally and its inner ``QLineEdit``
accepts URL drops by default, so both have ``setAcceptDrops(False)`` applied and
every drop is delivered to this tab: the first ``.epub``/``.txt`` fills the
selector; a manga-kind drop earns a cross-tab hint instead.
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
from anki_miner.gui.widgets.enhanced import FileSelector, ModernButton, SectionHeader
from anki_miner.gui.widgets.log_widget import LogWidget
from anki_miner.gui.widgets.progress_widget import ProgressWidget
from anki_miner.models.reading_queue import ReadingQueueItem
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from anki_miner.config import AnkiMinerConfig
    from anki_miner.interfaces.presenter import PresenterProtocol
    from anki_miner.orchestration import EpisodeProcessor

# File-selector filter glob for the Book File field. The human label ("Books")
# is tr()'d at call time; only the literal extension glob lives here.
_BOOK_FILTER_GLOB = "*.epub *.txt"

# Extensions this tab mines. A manga-kind drop (dirs always) earns a cross-tab
# hint instead of being mined here.
_NOVEL_EXTS = (".epub", ".txt")
_MANGA_EXTS = (".mokuro", ".cbz", ".zip")


class ReadingNovelsTab(_ReadingMiningTabBase):
    """Single-file novel mining sub-tab (no queue).

    Owns, via the base, at most one running
    :class:`~anki_miner.gui.workers.reading_queue_worker.ReadingQueueWorker`
    mining a single ephemeral item. Button state is purely derived from the
    worker handle by :meth:`_recompute_buttons`: idle shows Preview/Mine, a run
    swaps them for Cancel.

    Novels curation is table-only (D8): the base inherits the ``(None, None)``
    curation context — this tab does NOT override ``_build_curation_context``
    (only the manga sub-tab does, for its page-image context).
    """

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
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        layout.addWidget(self._create_novel_card())

        # Issue #65: opt-in per-item word curation popup (default off).
        self.review_words_checkbox = QCheckBox(self.tr("Review words before mining"))
        self.review_words_checkbox.setChecked(False)
        self.review_words_checkbox.setToolTip(self.tr("Show the word-selection popup before creating cards."))
        layout.addWidget(self.review_words_checkbox)

        layout.addWidget(self._progress_header(self.tr("Progress")))
        self.progress_widget = ProgressWidget()
        layout.addWidget(self.progress_widget)

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
        """Build a bold section-heading label for the progress bar."""
        header = QLabel(text)
        header.setObjectName("heading3")
        font = QFont()
        font.setPixelSize(FONT_SIZES.body)
        font.setWeight(QFont.Weight.Bold)
        header.setFont(font)
        return header

    def _create_novel_card(self) -> QFrame:
        """Novel card: book-file selector + Preview / Mine / Cancel."""
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout()
        card_layout.setSpacing(SPACING.sm)
        card_layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        card_layout.addWidget(SectionHeader(title=self.tr("Novel")))

        self.book_selector = FileSelector(
            label=self.tr("Book File:"),
            file_mode=True,
            file_filter=f"{self.tr('Books')} ({_BOOK_FILTER_GLOB})",
            label_width=field_label_width("Book File:"),
        )
        self.book_selector.setToolTip(self.tr("Select an .epub or .txt book to mine."))
        card_layout.addWidget(self.book_selector)

        button_row = QHBoxLayout()
        button_row.setSpacing(SPACING.sm)

        self.preview_button = ModernButton(self.tr("Preview"), variant="secondary")
        self.preview_button.setToolTip(self.tr("Preview the selected book — no cards created."))
        self.preview_button.clicked.connect(self._on_preview_clicked)
        button_row.addWidget(self.preview_button)

        self.mine_button = ModernButton(self.tr("Mine"), variant="primary")
        self.mine_button.setToolTip(self.tr("Mine the selected book into Anki cards."))
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
    # Drag-and-drop (tab-level: novels fill the selector; manga earns a hint)
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:
        """Accept a drag holding a book or a manga-kind source.

        Manga-kind sources are accepted too so the drop can be delivered and
        answered with the cross-tab hint (they never fill the selector here).
        """
        if event is None:
            return
        for url in urls_from_event(event):
            local = Path(url.toLocalFile())
            suffix = local.suffix.lower()
            if suffix in _NOVEL_EXTS or local.is_dir() or suffix in _MANGA_EXTS:
                event.acceptProposedAction()
                return

    def dropEvent(self, event: QDropEvent | None) -> None:
        """Fill the selector from the first dropped book; redirect manga drops."""
        if event is None:
            return
        manga_seen = False
        book_set = False
        for url in urls_from_event(event):
            local = Path(url.toLocalFile())
            suffix = local.suffix.lower()
            if suffix in _NOVEL_EXTS:
                if not book_set:
                    self.book_selector.set_path(str(local))
                    book_set = True
            elif local.is_dir() or suffix in _MANGA_EXTS:
                manga_seen = True
        if manga_seen:
            self.log_widget.append_info(self.tr("Manga is mined in the Manga tab."))
        event.acceptProposedAction()

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def _on_preview_clicked(self) -> None:
        """Preview — validate the book, then preview it."""
        self._start_run(preview_mode=True)

    def _on_mine_clicked(self) -> None:
        """Mine — validate the book, then mine it."""
        self._start_run(preview_mode=False)

    def _start_run(self, *, preview_mode: bool) -> None:
        """Validate the selected book and mine it as a single ephemeral item.

        The book is classified by ``detector.detect`` (one ref for a valid
        ``.epub``/``.txt``) into an ephemeral :class:`ReadingQueueItem` that is
        never stored — this tab has no queue. A ``True`` launch swaps the
        Preview/Mine buttons for Cancel and resets the progress bar.
        """
        if self.worker_thread is not None:
            return
        raw = self.book_selector.get_path().strip()
        path = Path(raw)
        if not raw or path.suffix.lower() not in _NOVEL_EXTS or not path.is_file():
            self.log_widget.append_warning(self.tr("Select a valid .epub or .txt book first."))
            return

        refs = self._detect_or_report(path)
        if refs is None:
            return

        ref = refs[0]
        ephemeral = ReadingQueueItem(source=ref, title=ref.title, kind=ref.kind)
        if self._launch_run([ephemeral], preview_mode=preview_mode):
            self._begin_run()

    def _begin_run(self) -> None:
        """Reset the progress bar and swap to the running button state."""
        self.progress_widget.reset()
        self.progress_widget.set_status(self.tr("Starting…"))
        self._recompute_buttons()

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
        self.progress_widget.set_status(self.tr("Cancelling…"))

    # ------------------------------------------------------------------
    # Per-item signal slots (READ-ONLY on item state — the worker owns it)
    # ------------------------------------------------------------------

    def _on_item_started(self, idx: int) -> None:
        """Seed the progress bar with the started book's title.

        READ-ONLY: the worker has already set ``status`` to PROCESSING before
        emitting this signal, so this only reflects current state.
        """
        item = self._item_at(idx)
        if item is None:
            return
        self._current_item_title = item.title
        # Status only — the composed bar never resets between items.
        self.progress_widget.set_status(tr_format(self.tr("Mining: %1"), item.title))

    def _on_item_progress(self, idx: int, label: str, pct: int) -> None:
        """Compose the book's percent into the run bar (pct < 0 holds the bar)."""
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
                self.progress_widget.set_status(status)
            return
        self.progress_widget.set_composed(idx, pct, len(self._run_items), status)

    def _on_item_finished(self, idx: int, result: object, error: object, attempts: int) -> None:
        """Log the outcome and forward a success result to the presenter.

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

    def _on_queue_finished(self) -> None:
        """No-op: a single book's outcome is already logged by ``_on_item_finished``."""

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

        Pure derived state: a live run hides Preview/Mine and shows Cancel; idle
        shows Preview/Mine and hides Cancel.
        """
        run_active = self.worker_thread is not None
        self.preview_button.setVisible(not run_active)
        self.mine_button.setVisible(not run_active)
        self.preview_button.setEnabled(not run_active)
        self.mine_button.setEnabled(not run_active)
        self.cancel_button.setVisible(run_active)
