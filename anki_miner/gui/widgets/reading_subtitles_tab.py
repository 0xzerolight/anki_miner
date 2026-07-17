"""Subtitles sub-tab of the Reading tab: multi-file subtitle-only mining.

Mines subtitle files (``.srt``/``.ass``/``.ssa``/``.vtt``) as text — no video,
so no screenshots and no extracted sentence audio (synthetic sentence TTS, if
enabled in Audio settings, still applies like any reading-sourced card) —
through the shared reading pipeline. Add files via the multi-select picker (or
drop several); **Mine** runs them sequentially as one job (one ephemeral
:class:`ReadingQueueItem` per file) through the shared
:class:`~anki_miner.gui.widgets._reading_mining_base._ReadingMiningTabBase`
lifecycle, composing per-file progress into one whole-run bar like the manga
sub-tab ("File N/M" in the status label). Word inspection happens via the
"Review words before mining" curation popup (no Preview — removed app-wide).

The worker OWNS the item lifecycle (it sets ``status``/``cards_created``/
``error_message`` on each item, on the worker thread, before emitting its
signals), so this tab's signal slots are READ-ONLY on item state: they update
the progress bar and log outcomes, never write status/cards/error.

Drag-drop routes through the tab, not the list widget: every dropped subtitle
file is appended to the list (deduped); a manga/novel drop earns a cross-tab
hint instead. Subtitle curation is table-only (the base ``(None, None)``
context — only manga overrides ``_build_curation_context``).

Class name is deliberately ``ReadingSubtitlesTab`` — distinct from the Tools
main tab's legacy ``SubtitlesTab`` class (whose display name is "Tools").
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.resources.styles import FONT_SIZES, SPACING
from anki_miner.gui.utils.qt_helpers import urls_from_event
from anki_miner.gui.widgets._reading_mining_base import _ReadingMiningTabBase
from anki_miner.gui.widgets.enhanced import ModernButton, SectionHeader
from anki_miner.gui.widgets.log_widget import LogWidget
from anki_miner.gui.widgets.progress_widget import ProgressWidget
from anki_miner.models import MiningOutcome, classify_result, result_error_text
from anki_miner.models.reading_queue import ReadingQueueItem
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from anki_miner.config import AnkiMinerConfig
    from anki_miner.interfaces.presenter import PresenterProtocol
    from anki_miner.orchestration import EpisodeProcessor

# Extensions this tab mines (mirrors detector._SUBTITLE_EXTS; no MicroDVD .sub
# — frame-based, needs a media fps). Manga/novel drops earn a cross-tab hint.
_SUBTITLE_EXTS = (".srt", ".ass", ".ssa", ".vtt")
_SUBTITLE_FILTER_GLOB = "*.srt *.ass *.ssa *.vtt"
_MANGA_EXTS = (".mokuro", ".cbz", ".zip")
_NOVEL_EXTS = (".epub", ".txt")

# Item-data role stamping each list row with its ephemeral ``ReadingQueueItem``
# at Mine time, so a mid-run Remove/Clear can route the removed row to the
# worker's identity-keyed skip channel (the worker iterates its own frozen
# snapshot, not the live list).
_ITEM_ROLE = Qt.ItemDataRole.UserRole


class ReadingSubtitlesTab(_ReadingMiningTabBase):
    """Multi-file subtitle mining sub-tab (sequential ephemeral items).

    Owns, via the base, at most one running
    :class:`~anki_miner.gui.workers.reading_queue_worker.ReadingQueueWorker`
    mining the listed subtitle files. Button state is purely derived from the
    worker handle by :meth:`_recompute_buttons`: idle shows Mine, a run swaps
    it for Cancel.

    Subtitle curation has no media context but shows the definition pane: the
    base's ``_build_curation_context`` returns ``(None, lookup_fn)`` from the
    worker's ``curation_processor`` — this tab does NOT override it.
    """

    def __init__(
        self,
        config: AnkiMinerConfig,
        processor: EpisodeProcessor | None = None,
        presenter: PresenterProtocol | None = None,
        parent: QWidget | None = None,
        stats_service: object | None = None,
    ) -> None:
        """Initialize the subtitles sub-tab.

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

        # The queue item the worker is mining right now, tracked by identity so
        # a mid-run Remove/Clear leaves the in-flight row in place (skipping an
        # already-started item is a no-op and yanking the watched row confuses).
        # READ-ONLY on item state: this only holds a reference — the worker owns
        # status/cards_created/error_message.
        self._running_item: ReadingQueueItem | None = None

        self._setup_ui()
        self._setup_drag_drop()
        # Route ALL drops through this tab's handler: QListWidget has its own
        # drop handling that would swallow URL drops before the tab sees them.
        self.file_list.setAcceptDrops(False)
        self._recompute_buttons()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """Build the tab layout: one Subtitles card, checkbox, one bar, log."""
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        layout.addWidget(self._create_subtitles_card())

        # Issue #65: opt-in per-item word curation popup (default off).
        self.review_words_checkbox = QCheckBox(self.tr("Review words before mining"))
        self.review_words_checkbox.setChecked(False)
        self.review_words_checkbox.setToolTip(
            self.tr("Show the word-selection popup for each file before creating cards.")
        )
        layout.addWidget(self.review_words_checkbox)

        # Single whole-run bar: per-file sweeps are composed into it
        # ((files done + file pct) / total), so a season run reads as one
        # continuous fill; the status label carries the active file + stage.
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
        """Build a bold section-heading label for the progress bar."""
        header = QLabel(text)
        header.setObjectName("heading3")
        font = QFont()
        font.setPixelSize(FONT_SIZES.body)
        font.setWeight(QFont.Weight.Bold)
        header.setFont(font)
        return header

    def _create_subtitles_card(self) -> QFrame:
        """Subtitles card: file list + Add/Remove/Clear + Mine/Cancel."""
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout()
        card_layout.setSpacing(SPACING.sm)
        card_layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        card_layout.addWidget(SectionHeader(title=self.tr("Subtitle Files")))

        note = QLabel(self.tr("Mines subtitle files as text — no screenshots or audio extracted from video."))
        note.setObjectName("caption")
        note.setWordWrap(True)
        card_layout.addWidget(note)

        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.file_list.setToolTip(self.tr("Subtitle files to mine, one card run per file, in list order."))
        self.file_list.setMinimumHeight(96)
        card_layout.addWidget(self.file_list)

        list_button_row = QHBoxLayout()
        list_button_row.setSpacing(SPACING.sm)

        self.add_files_button = ModernButton(self.tr("Add Files…"), variant="secondary")
        self.add_files_button.setToolTip(self.tr("Add subtitle files (.srt, .ass, .ssa, .vtt) to the list."))
        self.add_files_button.clicked.connect(self._on_add_files_clicked)
        list_button_row.addWidget(self.add_files_button)

        self.remove_selected_button = ModernButton(self.tr("Remove Selected"), variant="secondary")
        self.remove_selected_button.clicked.connect(self._on_remove_selected_clicked)
        list_button_row.addWidget(self.remove_selected_button)

        self.clear_button = ModernButton(self.tr("Clear"), variant="secondary")
        self.clear_button.clicked.connect(self._on_clear_clicked)
        list_button_row.addWidget(self.clear_button)

        list_button_row.addStretch()
        card_layout.addLayout(list_button_row)

        button_row = QHBoxLayout()
        button_row.setSpacing(SPACING.sm)

        self.mine_button = ModernButton(self.tr("Mine"), variant="primary")
        self.mine_button.setToolTip(self.tr("Mine the listed subtitle files into Anki cards."))
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
    # File-list management
    # ------------------------------------------------------------------

    def listed_paths(self) -> list[Path]:
        """The listed subtitle files, in list order."""
        items = (self.file_list.item(i) for i in range(self.file_list.count()))
        return [Path(item.text()) for item in items if item is not None]

    def _add_paths(self, paths: list[Path]) -> None:
        """Append subtitle files to the list, skipping duplicates."""
        existing = {str(p) for p in self.listed_paths()}
        for path in paths:
            text = str(path)
            if text in existing:
                continue
            existing.add(text)
            self.file_list.addItem(text)
        self._recompute_buttons()

    def _on_add_files_clicked(self) -> None:
        """Multi-select subtitle files into the list."""
        files, _ = QFileDialog.getOpenFileNames(
            self,
            self.tr("Add Subtitle Files"),
            "",
            f"{self.tr('Subtitles')} ({_SUBTITLE_FILTER_GLOB})",
        )
        if files:
            self._add_paths([Path(f) for f in files])

    def _on_remove_selected_clicked(self) -> None:
        """Remove the selected rows from the list.

        Mid-run this also routes each removed row to the worker's skip channel
        so the queue worker drops it before mining (it iterates its own frozen
        snapshot, not the live list) — mirroring the YouTube/audiobook tabs. The
        in-flight row is left in place: its item has already started, so skipping
        is a no-op and yanking the row the user is watching mine only confuses.
        """
        worker = self.worker_thread
        running = self._running_item
        for item in self.file_list.selectedItems():
            queue_item = item.data(_ITEM_ROLE)
            if worker is not None and queue_item is not None:
                if queue_item is running:
                    continue  # leave the row currently being mined in place
                worker.skip_item(queue_item)
            self.file_list.takeItem(self.file_list.row(item))
        self._recompute_buttons()

    def _on_clear_clicked(self) -> None:
        """Empty the file list.

        Idle, the whole list is dropped. Mid-run the in-flight row is preserved
        and every other listed row is routed to the worker's skip channel
        (mirroring the YouTube/audiobook tabs).
        """
        worker = self.worker_thread
        if worker is None:
            self.file_list.clear()
            self._recompute_buttons()
            return
        running = self._running_item
        # Reverse order so takeItem doesn't shift not-yet-visited row indices.
        for row in reversed(range(self.file_list.count())):
            list_item = self.file_list.item(row)
            if list_item is None:
                continue
            queue_item = list_item.data(_ITEM_ROLE)
            if queue_item is not None and queue_item is running:
                continue  # keep the row being mined
            if queue_item is not None:
                worker.skip_item(queue_item)
            self.file_list.takeItem(row)
        self._recompute_buttons()

    # ------------------------------------------------------------------
    # Drag-and-drop (tab-level: subtitles fill the list; manga/novels hint)
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:
        """Accept a drag holding a subtitle file or another reading kind.

        Manga/novel kinds are accepted too so the drop can be delivered and
        answered with the cross-tab hint (they never fill the list here).
        """
        if event is None:
            return
        for url in urls_from_event(event):
            local = Path(url.toLocalFile())
            suffix = local.suffix.lower()
            if suffix in _SUBTITLE_EXTS or local.is_dir() or suffix in _MANGA_EXTS or suffix in _NOVEL_EXTS:
                event.acceptProposedAction()
                return

    def dropEvent(self, event: QDropEvent | None) -> None:
        """Append ALL dropped subtitle files to the list; hint other kinds."""
        if event is None:
            return
        subtitle_paths: list[Path] = []
        manga_seen = False
        novel_seen = False
        for url in urls_from_event(event):
            local = Path(url.toLocalFile())
            suffix = local.suffix.lower()
            if suffix in _SUBTITLE_EXTS:
                subtitle_paths.append(local)
            elif local.is_dir() or suffix in _MANGA_EXTS:
                manga_seen = True
            elif suffix in _NOVEL_EXTS:
                novel_seen = True
        if subtitle_paths:
            self._add_paths(subtitle_paths)
        if manga_seen:
            self.log_widget.append_info(self.tr("Manga is mined in the Manga tab."))
        if novel_seen:
            self.log_widget.append_info(self.tr("Novels are mined in the Novels tab."))
        event.acceptProposedAction()

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def _on_mine_clicked(self) -> None:
        """Mine every listed file sequentially.

        Each file is classified by ``detector.detect`` (one subtitle ref per
        valid file) into an ephemeral :class:`ReadingQueueItem`; the items are
        never stored — the QListWidget is the only queue-like state. A ``True``
        launch swaps Mine for Cancel and resets the progress bar.
        """
        if self.worker_thread is not None:
            return
        paths = self.listed_paths()
        if not paths:
            self.log_widget.append_warning(self.tr("Add at least one subtitle file first."))
            return
        missing = [p for p in paths if not p.is_file()]
        if missing:
            self.log_widget.append_warning(tr_format(self.tr("File not found: %1"), str(missing[0])))
            return

        items: list[ReadingQueueItem] = []
        for row, path in enumerate(paths):
            refs = self._detect_or_report(path)
            if refs is None:
                return
            row_items = [ReadingQueueItem(source=ref, title=ref.title, kind=ref.kind) for ref in refs]
            items.extend(row_items)
            # Stamp the list row with its queue item so a mid-run Remove/Clear
            # can route it to worker.skip_item. Subtitle files always classify
            # to exactly one ref (detector._subtitle_ref), so row↔item is 1:1.
            list_item = self.file_list.item(row)
            if list_item is not None and row_items:
                list_item.setData(_ITEM_ROLE, row_items[0])

        if self._launch_run(items):
            self._begin_progress()

    def _begin_progress(self) -> None:
        """Reset the whole-run bar and swap to the running button state."""
        self._current_item_title = ""
        self.overall_progress_widget.reset()
        self.overall_progress_widget.set_status(self.tr("Starting…"))
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
        self.overall_progress_widget.set_status(self.tr("Cancelling…"))

    # ------------------------------------------------------------------
    # Per-item signal slots (READ-ONLY on item state — the worker owns it)
    # ------------------------------------------------------------------

    def _on_item_started(self, idx: int) -> None:
        """Seed the status label with the started file's title.

        READ-ONLY: the worker has already set ``status`` to PROCESSING before
        emitting this signal, so this only reflects current state.
        """
        item = self._item_at(idx)
        if item is None:
            return
        # Mark the in-flight item so a mid-run Remove/Clear leaves its row alone.
        self._running_item = item
        total = len(self._run_items)
        if total > 1:
            self._current_item_title = tr_format(self.tr("File %1/%2: %3"), idx + 1, total, item.title)
        else:
            self._current_item_title = item.title
        # Status only — the composed whole-run bar never resets between files.
        self.overall_progress_widget.set_status(self._current_item_title)

    def _on_item_progress(self, idx: int, label: str, pct: int) -> None:
        """Compose the file's percent into the whole-run bar.

        ``idx`` doubles as the count of files already finished (items run
        sequentially), so the composed value is monotone across file
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
        """Log the outcome and forward a success result to the presenter.

        READ-ONLY: the worker has already recorded ``status``/``cards_created``/
        ``error_message`` on the item before emitting this signal, so this slot
        only reads them and never writes them.
        """
        item = self._item_at(idx)
        if item is None:
            return
        # Capture the title before the identity compare below: `item is
        # self._running_item` re-widens `item` to include None (mypy 2.x),
        # which would defeat the None-guard above at the .title accesses.
        title = item.title
        # Nothing is in flight between this item finishing and the next starting,
        # so its row (and any not-yet-started rows) become freely removable.
        if item is self._running_item:
            self._running_item = None

        # A worker exception arrives as a non-None error; a non-raising return
        # (success, failure, or a cancel mid-mine) arrives as error=None with the
        # verdict inside the result. Classify both so a cancelled file isn't
        # logged as a green "Mined 0 cards." success.
        outcome = MiningOutcome.FAILED if error is not None else classify_result(result)
        if outcome is MiningOutcome.SUCCESS:
            cards = int(getattr(result, "cards_created", 0) or 0)
            self._record_item_result(result)
            self.log_widget.append_success(tr_format(self.tr("Mined %1: %2 cards."), title, cards))
            if self._presenter is not None:
                # Presenter forwarding is best-effort — the worker has already
                # recorded the result; a broken presenter slot shouldn't take
                # down the run.
                with contextlib.suppress(Exception):
                    self._presenter.show_processing_result(result)  # type: ignore[arg-type]
        elif outcome is MiningOutcome.CANCELLED:
            self.log_widget.append_info(tr_format(self.tr("Cancelled %1."), title))
        else:
            message = str(error) if error is not None else result_error_text(result)
            self.log_widget.append_error(tr_format(self.tr("Failed %1: %2."), title, message))

    def _on_queue_finished(self) -> None:
        """Log the whole-run outcome for a multi-file run.

        Single-file outcomes are already logged by ``_on_item_finished``.
        """
        if len(self._run_items) > 1:
            self.log_widget.append_info(tr_format(self.tr("Finished %1 subtitle files."), len(self._run_items)))

    def _after_run_cleanup(self) -> None:
        """Per-tab UI recovery after a run ends (called from the base cleanup slot).

        Restores the Cancel button, resets the progress bar, and recomputes
        button state. Runs on every run-exit path (success, cancel, exception).
        """
        self._running_item = None
        self.cancel_button.setText(self.tr("Cancel"))
        self.cancel_button.setEnabled(True)
        self._apply_terminal_bar_state(self.overall_progress_widget)
        self._recompute_buttons()

    # ------------------------------------------------------------------
    # Button recomputation
    # ------------------------------------------------------------------

    def _recompute_buttons(self) -> None:
        """Refresh button state from the worker handle and the file list.

        Pure derived state: a live run hides Mine and shows Cancel; idle shows
        Mine and hides Cancel. Add locks during a run (a new row would have no
        queue item), but Remove/Clear stay enabled whenever the list is
        non-empty — mid-run they drop rows through the worker's skip channel.
        """
        run_active = self.worker_thread is not None
        has_items = self.file_list.count() > 0
        self.mine_button.setVisible(not run_active)
        self.mine_button.setEnabled(not run_active)
        self.cancel_button.setVisible(run_active)
        self.add_files_button.setEnabled(not run_active)
        self.remove_selected_button.setEnabled(has_items)
        self.clear_button.setEnabled(has_items)
