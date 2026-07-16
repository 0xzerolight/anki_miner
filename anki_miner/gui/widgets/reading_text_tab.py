"""Text sub-tab of the Reading tab: pasted-text mining.

Mines one pasted snippet per run — no file, no screenshots, no extracted audio
(synthetic sentence TTS, if enabled in Audio settings, still applies like any
reading-sourced card) — through the shared reading pipeline. Paste text,
**Mine** launches a single ephemeral :class:`ReadingQueueItem` carrying a
pathless ``kind="text"`` ref (the text is snapshotted at Mine time, so the
edit stays usable mid-run) through the shared
:class:`~anki_miner.gui.widgets._reading_mining_base._ReadingMiningTabBase`
lifecycle. Identity is deliberately constant ("Text"/"Text") — see
``services/reading/text_source.py``.

The worker OWNS the item lifecycle (it sets ``status``/``cards_created``/
``error_message`` on the item, on the worker thread, before emitting its
signals), so this tab's signal slots are READ-ONLY on item state.

No drag-drop overrides: QPlainTextEdit accepts text drops natively. Text
curation is table-only (the base ``(None, lookup_fn)`` context — only manga
overrides ``_build_curation_context``).
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.resources.styles import FONT_SIZES, SPACING
from anki_miner.gui.widgets._reading_mining_base import _ReadingMiningTabBase
from anki_miner.gui.widgets.enhanced import ModernButton, SectionHeader
from anki_miner.gui.widgets.log_widget import LogWidget
from anki_miner.gui.widgets.progress_widget import ProgressWidget
from anki_miner.models.reading import ReadingSourceRef
from anki_miner.models.reading_queue import ReadingQueueItem
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from anki_miner.config import AnkiMinerConfig
    from anki_miner.interfaces.presenter import PresenterProtocol
    from anki_miner.orchestration import EpisodeProcessor


class ReadingTextTab(_ReadingMiningTabBase):
    """Pasted-text mining sub-tab (one ephemeral item per run).

    Owns, via the base, at most one running
    :class:`~anki_miner.gui.workers.reading_queue_worker.ReadingQueueWorker`
    mining the pasted text. Button state is purely derived from the worker
    handle and the edit content by :meth:`_recompute_buttons`: idle shows
    Mine (enabled only when non-blank text is present), a run swaps it for
    Cancel.

    Text curation has no media context but shows the definition pane: the
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
        """Initialize the text sub-tab.

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
        self._recompute_buttons()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """Build the tab layout: one Text card, checkbox, one bar, log."""
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        layout.addWidget(self._create_text_card())

        # Issue #65: opt-in word curation popup (default off).
        self.review_words_checkbox = QCheckBox(self.tr("Review words before mining"))
        self.review_words_checkbox.setChecked(False)
        self.review_words_checkbox.setToolTip(self.tr("Show the word-selection popup before creating cards."))
        layout.addWidget(self.review_words_checkbox)

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

    def _create_text_card(self) -> QFrame:
        """Text card: paste area + Mine/Cancel."""
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout()
        card_layout.setSpacing(SPACING.sm)
        card_layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        card_layout.addWidget(SectionHeader(title=self.tr("Pasted Text")))

        note = QLabel(self.tr("Paste Japanese text and mine it into Anki cards — no screenshots or audio."))
        note.setObjectName("caption")
        note.setWordWrap(True)
        card_layout.addWidget(note)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText(self.tr("Paste text here…"))
        self.text_edit.setMinimumHeight(140)
        self.text_edit.textChanged.connect(self._recompute_buttons)
        card_layout.addWidget(self.text_edit)

        button_row = QHBoxLayout()
        button_row.setSpacing(SPACING.sm)

        self.mine_button = ModernButton(self.tr("Mine"), variant="primary")
        self.mine_button.setToolTip(self.tr("Mine the pasted text into Anki cards."))
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
    # Run lifecycle
    # ------------------------------------------------------------------

    def _on_mine_clicked(self) -> None:
        """Mine the pasted text as one ephemeral queue item.

        The ref snapshots the text at click time, so mid-run edits are
        harmless. A ``True`` launch swaps Mine for Cancel and resets the bar.
        """
        if self.worker_thread is not None:
            return
        text = self.text_edit.toPlainText()
        if not text.strip():
            self.log_widget.append_warning(self.tr("Paste some text first."))
            return

        # Constant identity by design (see text_source.py) — untranslated data
        # constant, like aozora's series="Books".
        ref = ReadingSourceRef(kind="text", title="Text", text=text)
        item = ReadingQueueItem(source=ref, title=ref.title, kind=ref.kind)

        if self._launch_run([item]):
            self._begin_progress()

    def _begin_progress(self) -> None:
        """Reset the run bar and swap to the running button state."""
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
        """Seed the status label for the (single) started item."""
        if self._item_at(idx) is None:
            return
        self.overall_progress_widget.set_status(self.tr("Mining pasted text…"))

    def _on_item_progress(self, idx: int, label: str, pct: int) -> None:
        """Drive the run bar; ``pct < 0`` holds it with a status update."""
        status = label or None
        if pct < 0:
            if status:
                self.overall_progress_widget.set_status(status)
            return
        self.overall_progress_widget.set_composed(idx, pct, len(self._run_items), status)

    def _on_item_finished(self, idx: int, result: object, error: object, attempts: int) -> None:
        """Log the outcome and forward a success result to the presenter.

        READ-ONLY: the worker has already recorded ``status``/``cards_created``/
        ``error_message`` on the item before emitting this signal.
        """
        if self._item_at(idx) is None:
            return
        if error is None:
            cards = int(getattr(result, "cards_created", 0) or 0)
            self._record_item_result(result)
            self.log_widget.append_success(tr_format(self.tr("Mined %1 cards."), cards))
            if self._presenter is not None:
                # Presenter forwarding is best-effort — the worker has already
                # recorded the result; a broken presenter slot shouldn't take
                # down the run.
                with contextlib.suppress(Exception):
                    self._presenter.show_processing_result(result)  # type: ignore[arg-type]
        else:
            self.log_widget.append_error(tr_format(self.tr("Failed: %1."), error))

    def _on_queue_finished(self) -> None:
        """Single-item runs are already logged by ``_on_item_finished``."""

    def _after_run_cleanup(self) -> None:
        """Per-tab UI recovery after a run ends (called from the base cleanup slot).

        Restores the Cancel button, resets the progress bar, and recomputes
        button state. Runs on every run-exit path (success, cancel, exception).
        The pasted text is deliberately retained for re-mining with tweaks.
        """
        self.cancel_button.setText(self.tr("Cancel"))
        self.cancel_button.setEnabled(True)
        self._apply_terminal_bar_state(self.overall_progress_widget)
        self._recompute_buttons()

    # ------------------------------------------------------------------
    # Button recomputation
    # ------------------------------------------------------------------

    def _recompute_buttons(self) -> None:
        """Refresh button state from the worker handle and the text edit.

        Pure derived state: a live run hides Mine and shows Cancel; idle shows
        Mine, enabled only when non-blank text is present. The edit stays
        usable mid-run (the ref snapshotted the text at Mine time).
        """
        run_active = self.worker_thread is not None
        has_text = bool(self.text_edit.toPlainText().strip())
        self.mine_button.setVisible(not run_active)
        self.mine_button.setEnabled(not run_active and has_text)
        self.cancel_button.setVisible(run_active)
