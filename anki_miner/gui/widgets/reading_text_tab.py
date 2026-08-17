"""Text sub-tab of the Reading tab: pasted-text mining.

Mines one pasted snippet per run — no file, no extracted audio (synthetic
sentence TTS, if enabled in Audio settings, still applies like any
reading-sourced card) — through the shared reading pipeline. Pasted text has no
page of its own, so the one optional Card Image the user picks here rides on
the ref as ``image_root`` and lands in the Picture field of every card from the
run (``services/reading/text_source.py``). Paste text,
**Mine** launches a single ephemeral :class:`ReadingQueueItem` carrying a
pathless ``kind="text"`` ref (the text is snapshotted at Mine time, so the
edit stays usable mid-run) through the shared
:class:`~anki_miner.gui.widgets._reading_mining_base._ReadingMiningTabBase`
lifecycle. Identity is deliberately constant ("Text"/"Text") — see
``services/reading/text_source.py``.

The worker OWNS the item lifecycle (it sets ``status``/``cards_created``/
``error_message`` on the item, on the worker thread, before emitting its
signals), so this tab's signal slots are READ-ONLY on item state.

No tab-level drag-drop overrides: QPlainTextEdit accepts text drops natively.
A dragged FILE is refused by :class:`_RefuseFileDrops` rather than inserted as
its own path (D50). Text curation is table-only (the base ``(None, lookup_fn)``
context — only manga overrides ``_build_curation_context``).
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import QT_TRANSLATE_NOOP, QEvent, QObject
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QLabel,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.gui.resources.styles import FONT_SIZES, SPACING, TYPOGRAPHY
from anki_miner.gui.utils.fonts import JAPANESE_BODY, apply_japanese_block_format, apply_japanese_font
from anki_miner.gui.widgets._reading_mining_base import _ReadingMiningTabBase
from anki_miner.gui.widgets.base import PageWidth, configure_card_layout, field_label_width
from anki_miner.gui.widgets.enhanced import FileSelector, ModernButton, SectionHeader, accepts_suffixes
from anki_miner.gui.widgets.log_widget import LogWidget
from anki_miner.gui.widgets.progress_widget import ProgressWidget
from anki_miner.models import MiningOutcome, result_error_text
from anki_miner.models.mining_queue import ReadyItemStatus
from anki_miner.models.reading import ReadingSourceRef
from anki_miner.models.reading_queue import ReadingQueueItem
from anki_miner.services.reading.images import validate_card_image
from anki_miner.utils.i18n import tr_format

#: Suffixes the picker offers and accepts on a drop. ``prepare_card_image``
#: re-encodes everything to JPEG, so the list is about what Pillow can read.
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")
_IMAGE_FILTER_GLOB = " ".join(f"*{ext}" for ext in _IMAGE_EXTS)

if TYPE_CHECKING:
    from anki_miner.config import AnkiMinerConfig
    from anki_miner.interfaces.presenter import PresenterProtocol
    from anki_miner.orchestration import EpisodeProcessor


class _RefuseFileDrops(QObject):
    """Let text land in an editor, and refuse a dragged file out loud (D50).

    ``QPlainTextEdit`` accepts a file drag and inserts its path as text, so the
    drop looks accepted and the user mines a file name. Installed on the editor
    and its viewport, this eats file drags at every stage -- so the cursor never
    promises a drop that would be wrong -- and reports the reason once, on
    release. Plain-text drags are untouched.
    """

    #: The three stages a file drag reaches. All three are eaten together, or
    #: the cursor says "yes" right up to the moment the drop is refused.
    _DRAG_STAGES = (QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop)

    def __init__(self, parent: QObject, *, reason: str, report: Callable[[str], None]) -> None:
        """Initialize the filter.

        Args:
            parent: Owner; keeps the filter alive as long as the editor.
            reason: The already-translated sentence shown on refusal.
            report: Where the reason goes -- the tab's Activity log.
        """
        super().__init__(parent)
        self._reason = reason
        self._report = report

    def eventFilter(self, obj: QObject | None, event: QEvent | None) -> bool:  # noqa: N802 - Qt override
        """Eat file drags; pass everything else, including text drags, through."""
        if event is None or event.type() not in self._DRAG_STAGES:
            return super().eventFilter(obj, event)
        mime = getattr(event, "mimeData", None)
        data = mime() if callable(mime) else None
        if data is None or not data.hasUrls():
            return super().eventFilter(obj, event)
        event.ignore()
        if event.type() == QEvent.Type.Drop:
            self._report(self._reason)
        return True


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

    #: A label beside its control; a wider window buys gutters, not longer inputs.
    PAGE_WIDTH = PageWidth.PAGE

    #: Published so this screen's Cancel gets a live wait clock and the
    #: pinned bar gets a stage and a progress bar (D17, D22).
    TASK_ID = "queue.reading.text"
    TASK_OWNER = CapabilityTarget("reading", "text")
    #: Name this run carries away from this screen.
    TASK_TITLE = QT_TRANSLATE_NOOP("ReadingTab", "Text mining")

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

        container = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        # LogWidget: own header + Copy/Clear actions; install_workflow_shell
        # moves it into the Activity drawer (D6). Built before the card because
        # the card's controls report into it.
        self.log_widget = LogWidget()

        layout.addWidget(self._create_text_card())

        # Issue #65: opt-in word curation popup (default off).
        self.review_words_checkbox = QCheckBox(self.tr("Review words before mining"))
        self.review_words_checkbox.setChecked(False)
        self.review_words_checkbox.setToolTip(self.tr("Show the word-selection popup before creating cards."))
        layout.addWidget(self.review_words_checkbox)

        layout.addWidget(self._progress_header(self.tr("Progress")))
        self.overall_progress_widget = ProgressWidget()
        layout.addWidget(self.overall_progress_widget)
        # The durable end state of this same card (D20). Pasted text is always
        # one item, so the receipt never needs a noun to count.
        self._install_receipt(layout, self.overall_progress_widget)

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
        configure_card_layout(card_layout)

        card_layout.addWidget(SectionHeader(title=self.tr("Pasted Text")))

        note = QLabel(self.tr("Paste Japanese text and mine it into Anki cards — no audio is extracted."))
        note.setObjectName("caption")
        note.setWordWrap(True)
        card_layout.addWidget(note)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText(self.tr("Paste text here…"))
        self.text_edit.setMinimumHeight(140)
        # Text drops land natively; a dragged FILE used to be inserted as its
        # own path, which reads as "accepted" and mines a file name (D50). The
        # filter refuses those and says so in Activity.
        self._file_drop_filter = _RefuseFileDrops(
            self.text_edit,
            reason=self.tr("Drop or paste text here; files are not supported."),
            report=lambda reason: self.log_widget.append_warning(reason),
        )
        self.text_edit.installEventFilter(self._file_drop_filter)
        viewport = self.text_edit.viewport()
        if viewport is not None:
            viewport.installEventFilter(self._file_drop_filter)
        # What the user pastes here is the Japanese they came to mine, not
        # interface chrome: the Japanese face, a reading size, and the looser
        # leading (decision D45-B).
        apply_japanese_font(self.text_edit, role=JAPANESE_BODY)
        apply_japanese_block_format(self.text_edit.document())
        self.text_edit.textChanged.connect(self._keep_japanese_leading)
        self.text_edit.textChanged.connect(self._recompute_buttons)
        card_layout.addWidget(self.text_edit)

        # Pasted text has no page of its own, so the one picture every card in
        # the run shares is a deliberate pick, not an extraction. Optional by
        # design: an empty field mines imageless cards exactly as before.
        self.image_selector = FileSelector(
            label=self.tr("Card Image:"),
            file_mode=True,
            file_filter=f"{self.tr('Images')} ({_IMAGE_FILTER_GLOB})",
            label_width=field_label_width(self.tr("Card Image:")),
            history_key="reading.text.inputs",
            drop_validator=accepts_suffixes(_IMAGE_EXTS, self.tr("This field takes an image file.")),
        )
        self.image_selector.setToolTip(
            self.tr("Optional. This image goes in the Picture field of every card from this text.")
        )
        self.image_selector.drop_rejected.connect(self.log_widget.append_warning)
        card_layout.addWidget(self.image_selector)

        # Mine and Cancel live in the pinned bar (D6), so a long paste cannot
        # push the run button off the screen.
        self.mine_button = ModernButton(self.tr("Mine"), variant="primary")
        self.mine_button.setToolTip(self.tr("Mine the pasted text into Anki cards."))
        self.mine_button.clicked.connect(self._on_mine_clicked)

        self.cancel_button = ModernButton(self.tr("Cancel"), variant="secondary")
        self.cancel_button.setToolTip(self.tr("Cancel the active run."))
        self.cancel_button.clicked.connect(self._on_cancel_clicked)
        self.cancel_button.hide()

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

        usable, image_root = self._picked_image()
        if not usable:  # picked but unreadable — the user already heard why
            return

        # Constant identity by design (see text_source.py) — untranslated data
        # constant, like aozora's series="Books".
        ref = ReadingSourceRef(kind="text", title="Text", text=text, image_root=image_root)
        item = ReadingQueueItem(source=ref, title=ref.title, kind=ref.kind)

        if self._launch_run([item]):
            self._begin_progress()

    def _picked_image(self) -> tuple[bool, Path | None]:
        """Resolve the optional card image as ``(usable, path)``.

        An empty field and a broken pick must not be confused: ``(True, None)``
        = nothing picked, mine imageless; ``(True, path)`` = use it;
        ``(False, None)`` = the user picked something this pipeline cannot
        read, so the run is refused now instead of after mining the whole text.
        """
        raw = self.image_selector.path_or_none()
        if raw is None:
            return True, None
        # NEVER strip a path — trailing whitespace can be part of a real name.
        path = Path(raw)
        if not validate_card_image(path):
            self.log_widget.append_warning(
                self.tr("That image cannot be read. Pick another, or clear the field to mine without one.")
            )
            return False, None
        return True, path

    def _begin_progress(self) -> None:
        """Reset the run bar and swap to the running button state."""
        self.overall_progress_widget.reset()
        self.overall_progress_widget.set_status(self.tr("Starting…"))
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
        self._freeze_run_bar(self.overall_progress_widget)

    # ------------------------------------------------------------------
    # Per-item signal slots (READ-ONLY on item state — the worker owns it)
    # ------------------------------------------------------------------

    def _on_item_started(self, idx: int) -> None:
        """Seed the status label for the (single) started item."""
        if self._item_at(idx) is None:
            return
        self.overall_progress_widget.set_status(self.tr("Mining pasted text…"))

    def _on_item_progress(self, idx: int, label: str) -> None:
        """Say what the run is doing. The bar counts finished items only."""
        if label:
            self.overall_progress_widget.set_status(label)

    def _on_item_finished(self, idx: int, result: object, error: object, attempts: int) -> None:
        """Log the outcome and forward a success result to the presenter.

        READ-ONLY: the worker has already recorded ``status``/``cards_created``/
        ``error_message`` on the item before emitting this signal.
        """
        if self._item_at(idx) is None:
            return
        # A worker exception arrives as a non-None error string; a non-raising
        # return (success, failure, or a cancel mid-mine) arrives as error=None
        # with the verdict inside the result. Classify both so a cancelled run
        # isn't logged as a green "Mined 0 cards." success.
        outcome = self._record_item_outcome(result, error)
        if outcome is MiningOutcome.SUCCESS:
            cards = int(getattr(result, "cards_created", 0) or 0)
            self.log_widget.append_success(tr_format(self.tr("Mined %1 cards."), cards))
            if self._presenter is not None:
                # Presenter forwarding is best-effort — the worker has already
                # recorded the result; a broken presenter slot shouldn't take
                # down the run.
                with contextlib.suppress(Exception):
                    self._presenter.show_processing_result(result)  # type: ignore[arg-type]
        elif outcome is MiningOutcome.CANCELLED:
            self.log_widget.append_info(self.tr("Cancelled."))
        else:
            message = str(error) if error is not None else result_error_text(result)
            self.log_widget.append_error(tr_format(self.tr("Failed: %1."), message))

        # The bar's only honest denominator: items that reached a terminal state
        # out of items in the run.
        done = sum(1 for i in self._run_items if i.status in (ReadyItemStatus.COMPLETED, ReadyItemStatus.ERROR))
        self.overall_progress_widget.set_composed(done, 0, len(self._run_items))

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

    def _keep_japanese_leading(self) -> None:
        """Restore the Japanese leading after a wholesale text replacement.

        Typing and pasting inherit the block format from the block being split,
        so nothing is needed there. ``setPlainText`` replaces every block and
        drops it. The guard makes the common case a single comparison, and stops
        the re-merge from re-entering through its own ``textChanged``.
        """
        document = self.text_edit.document()
        if document is None:
            return
        if document.firstBlock().blockFormat().lineHeight() == TYPOGRAPHY.japanese_leading_percent:
            return
        apply_japanese_block_format(document)

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
