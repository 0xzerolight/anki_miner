"""Anki Deck sub-tab of the Reading tab: mine an existing Anki deck (Issue #131).

A premade subs2srs / movies2anki / asbplayer deck already in Anki carries a
subtitle line, its audio clip and a picture on every card, but no target word.
This screen mines those lines the way the Subtitle Files sub-tab mines cues:
pick a deck, check the auto-detected fields, **Mine**. One ephemeral
:class:`ReadingQueueItem` carries a pathless ``kind="deck"`` ref through the
shared :class:`~anki_miner.gui.widgets._reading_mining_base._ReadingMiningTabBase`
lifecycle; ``services/reading/anki_deck_source.py`` reads the notes, and each
mined card reuses its source card's audio, picture and translation. The source
deck is never changed.

The deck list and the field inspection are AnkiConnect calls, so both run off
the GUI thread (``run_off_thread``, whose global registry joins them at app
close); the inspection carries a generation guard so a slow answer for a deck
the user has already moved off never lands.

The worker OWNS the item lifecycle (it sets ``status``/``cards_created``/
``error_message`` on the item before emitting its signals), so this tab's
signal slots are READ-ONLY on item state.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING

from PyQt6.QtCore import QT_TRANSLATE_NOOP, Qt
from PyQt6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.qt_helpers import install_no_scroll_on_inputs, urls_from_event
from anki_miner.gui.utils.run_off_thread import run_off_thread, still_running
from anki_miner.gui.widgets._reading_mining_base import _ReadingMiningTabBase
from anki_miner.gui.widgets.base import PageWidth, ScreenIssue, cap_row_field, configure_card_layout, field_label_width
from anki_miner.gui.widgets.dialogs.word_curation_dialog import CurationMediaContext
from anki_miner.gui.widgets.enhanced import ModernButton
from anki_miner.gui.widgets.log_widget import LogWidget
from anki_miner.gui.widgets.progress_widget import ProgressWidget
from anki_miner.languages.registry import config_language, get_profile
from anki_miner.models import MiningOutcome, result_error_text
from anki_miner.models.mining_queue import ReadyItemStatus
from anki_miner.models.reading import DeckFieldMap, ReadingSourceRef
from anki_miner.models.reading_queue import ReadingQueueItem
from anki_miner.services.anki_service import AnkiService
from anki_miner.services.deck_filter import DeckInspection, inspect_deck
from anki_miner.services.reading.anki_deck_source import suggest_field_map
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from anki_miner.config import AnkiMinerConfig
    from anki_miner.gui.workers.base_worker import SingleCallWorker
    from anki_miner.interfaces.presenter import PresenterProtocol
    from anki_miner.orchestration import EpisodeProcessor

logger = logging.getLogger(__name__)


class ReadingDeckTab(_ReadingMiningTabBase):
    """Existing-Anki-deck mining sub-tab (one ephemeral item per run).

    Button state is derived by :meth:`_recompute_buttons`: idle shows Mine,
    enabled once a deck and its sentence field are picked; a run swaps it for
    Cancel. Curation shows each card's picture and plays its clip when the deck
    has either (see :meth:`_build_curation_context`).
    """

    #: A label beside its control; a wider window buys gutters, not longer inputs.
    PAGE_WIDTH = PageWidth.PAGE

    #: Published so this screen's Cancel gets a live wait clock and the
    #: pinned bar gets a stage and a progress bar (D17, D22).
    TASK_ID = "queue.reading.deck"
    TASK_OWNER = CapabilityTarget("reading", "deck")
    #: Name this run carries away from this screen.
    TASK_TITLE = QT_TRANSLATE_NOOP("ReadingTab", "Anki deck mining")

    def __init__(
        self,
        config: AnkiMinerConfig,
        processor: EpisodeProcessor | None = None,
        presenter: PresenterProtocol | None = None,
        parent: QWidget | None = None,
        stats_service: object | None = None,
    ) -> None:
        """Initialize the Anki Deck sub-tab.

        Args:
            config: Frozen application configuration.
            processor: Episode processor (reused across runs within this tab).
                May be ``None``; the first run builds one lazily.
            presenter: Optional presenter for routing results.
            parent: Optional parent widget.
            stats_service: Optional ``StatsService`` reused across lazy
                processor rebuilds so these runs land in analytics.
        """
        super().__init__(config, processor, presenter, parent, stats_service)
        # The deck list is fetched again on every show: the whole point of the
        # screen is a deck the user may have imported into Anki a minute ago.
        self._deck_worker: SingleCallWorker | None = None
        self._deck_fetch_failed = False
        self._inspect_generation = 0
        # None until the picked deck has been read (an empty deck reads as 0).
        self._note_count: int | None = None
        self._deck_fetch_issue: ScreenIssue | None = None
        self._setup_ui()
        self._setup_drag_drop()
        self._recompute_buttons()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """Build the tab layout: one Deck card, checkbox, one bar, log."""
        scroll_area = QScrollArea()

        container = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        # LogWidget: own header + Copy/Clear actions; install_workflow_shell
        # moves it into the Activity drawer (D6).
        self.log_widget = LogWidget(source=self.TASK_ID or type(self).__name__)

        layout.addWidget(self._create_deck_card())

        # Issue #65: opt-in word curation popup (default off).
        self.review_words_checkbox = QCheckBox(self.tr("Review words before mining"))
        self._bind_review_words_checkbox()
        self.review_words_checkbox.setToolTip(self.tr("Show the word-selection popup before creating cards."))
        layout.addWidget(self.review_words_checkbox)

        # D1: the pinned bar is the one progress surface; this widget is the
        # run's hidden state holder and the receipt's anchor.
        self.overall_progress_widget = ProgressWidget()
        self.overall_progress_widget.hide()
        layout.addWidget(self.overall_progress_widget)
        # The durable end state of this same card (D20). A run is one deck.
        self._install_receipt(layout, self.overall_progress_widget)

        container.setLayout(layout)
        install_no_scroll_on_inputs(container)

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

    def _create_deck_card(self) -> QFrame:
        """Deck card: deck picker, four field pickers, status line."""
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout()
        configure_card_layout(card_layout)

        note = QLabel(
            self.tr(
                "Mine the sentences of a deck already in Anki, such as a subs2srs deck. "
                "Each new card reuses that card's audio and picture. The deck itself is not changed."
            )
        )
        note.setObjectName("caption")
        note.setWordWrap(True)
        card_layout.addWidget(note)

        labels = (
            self.tr("Deck:"),
            self.tr("Sentence from:"),
            self.tr("Audio from:"),
            self.tr("Picture from:"),
            self.tr("Translation from:"),
        )
        label_width = field_label_width(*labels)

        self.deck_combo = QComboBox()
        self.deck_combo.addItem(self.tr("Select a deck…"))
        self.deck_combo.setToolTip(self.tr("The deck to mine. Its subdecks are included."))
        self.deck_combo.currentIndexChanged.connect(self._on_deck_changed)

        self.sentence_combo = QComboBox()
        self.sentence_combo.addItem(self.tr("Select a field…"))
        self.sentence_combo.setToolTip(self.tr("The field that holds the subtitle line to mine."))
        none = self.tr("(none)")
        self.audio_combo = QComboBox()
        self.audio_combo.addItem(none)
        self.audio_combo.setToolTip(self.tr("The field with the line's audio clip."))
        self.picture_combo = QComboBox()
        self.picture_combo.addItem(none)
        self.picture_combo.setToolTip(self.tr("The field with the line's picture."))
        self.translation_combo = QComboBox()
        self.translation_combo.addItem(none)
        self.translation_combo.setToolTip(self.tr("The field with the line's translation, if the deck has one."))
        self.sentence_combo.currentIndexChanged.connect(self._recompute_buttons)

        # A17: the deck comes first, on its own row, capped like every other
        # field; the note count sits right under it.
        deck_row = QHBoxLayout()
        deck_row.setSpacing(SPACING.sm)
        deck_label = QLabel(labels[0])
        deck_label.setObjectName("field-label")
        deck_label.setFixedWidth(label_width)
        deck_label.setBuddy(self.deck_combo)
        deck_row.addWidget(deck_label)
        deck_row.addWidget(self.deck_combo)
        cap_row_field(self.deck_combo, label_width, deck_row.spacing())
        deck_row.addStretch()
        card_layout.addLayout(deck_row)

        self.status_label = QLabel("")
        self.status_label.setObjectName("caption")
        self.status_label.setWordWrap(True)
        card_layout.addWidget(self.status_label)

        # A17: the four source rows appear once the deck has been read, already
        # filled by auto-detect (the override combos stay a locked design).
        self.fields_widget = QWidget()
        grid = QGridLayout(self.fields_widget)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(SPACING.sm)
        grid.setVerticalSpacing(SPACING.xs)
        for row, (text, combo) in enumerate(zip(labels[1:], self._field_combos(), strict=True)):
            label = QLabel(text)
            label.setObjectName("field-label")
            label.setFixedWidth(label_width)
            label.setBuddy(combo)
            grid.addWidget(label, row, 0)
            grid.addWidget(combo, row, 1, Qt.AlignmentFlag.AlignLeft)
            cap_row_field(combo, label_width, grid.horizontalSpacing())
        grid.setColumnStretch(1, 1)
        self.fields_widget.hide()
        card_layout.addWidget(self.fields_widget)
        self._set_field_combos_enabled(False)

        # Mine and Cancel live in the pinned bar (D6).
        self.mine_button = ModernButton(self.tr("Mine"), variant="primary")
        self.mine_button.setToolTip(self.tr("Mine the deck's sentences into Anki cards."))
        self.mine_button.clicked.connect(self._on_mine_clicked)

        self.cancel_button = ModernButton(self.tr("Cancel"), variant="secondary")
        self.cancel_button.setToolTip(self.tr("Cancel the active run."))
        self.cancel_button.clicked.connect(self._on_cancel_clicked)
        self.cancel_button.hide()

        card.setLayout(card_layout)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        return card

    def _field_combos(self) -> tuple[QComboBox, ...]:
        return (self.sentence_combo, self.audio_combo, self.picture_combo, self.translation_combo)

    def _set_field_combos_enabled(self, enabled: bool) -> None:
        for combo in self._field_combos():
            combo.setEnabled(enabled)

    # ------------------------------------------------------------------
    # Deck list (fetched on every show, refilled in place)
    # ------------------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self.ensure_decks()

    def ensure_decks(self) -> None:
        """Fetch the deck list unless a fetch is already in flight.

        Not latched on the first answer: a deck imported into Anki while Anki
        Miner is open must show up the next time this screen is looked at. One
        ``deckNames`` call is cheap. Also the slot for
        ``MainWindow.anki_reachable``: when Anki starts after Anki Miner, the
        health sweep that finds it is the only retry while this tab stays on
        screen.
        """
        if still_running(self._deck_worker):
            return
        self._load_decks()

    def _load_decks(self) -> None:
        """Start the deck-list fetch (the one seam screen-walking tests stub)."""
        try:
            service = AnkiService(self.config)
        except ValueError as exc:
            logger.warning("Anki Deck deck fetch skipped: missing=field_mapping error=%s", exc)
            return
        self._deck_worker = run_off_thread(
            self,
            service.get_deck_names,
            self._on_decks_fetched,
            self._on_deck_fetch_error,
        )

    def _on_decks_fetched(self, decks: object) -> None:
        names = [str(deck) for deck in decks] if isinstance(decks, list) else []
        listed = [self.deck_combo.itemText(i) for i in range(1, self.deck_combo.count())]
        if not names:
            # Anki closed after a list arrived: keep the list the user is
            # looking at; the run itself reports an unreachable Anki.
            if not listed:
                self._deck_fetch_failed = True
                # A17: nothing to pick from, so the picker is off and the
                # banner says why.
                self.deck_combo.setEnabled(False)
                self._deck_fetch_issue = ScreenIssue(
                    summary=self.tr("Couldn't fetch deck names from Anki. Is Anki running?")
                )
                self.show_screen_issue(self._deck_fetch_issue)
            return
        if self._deck_fetch_failed:
            self._deck_fetch_failed = False
            self.deck_combo.setEnabled(True)
            banner = self.issue_banner()
            # Only the complaint this fetch raised; anything else stays up.
            if banner is not None and banner.current_issue() is self._deck_fetch_issue:
                self.clear_screen_issue()
            self._deck_fetch_issue = None
        if names == listed:
            return
        picked = self._selected_deck()
        # Refill without firing _on_deck_changed, so a deck that is still
        # there keeps its inspected field picks.
        self.deck_combo.blockSignals(True)
        try:
            while self.deck_combo.count() > 1:
                self.deck_combo.removeItem(self.deck_combo.count() - 1)
            self.deck_combo.addItems(names)
            index = self.deck_combo.findText(picked, Qt.MatchFlag.MatchExactly) if picked else -1
            self.deck_combo.setCurrentIndex(max(index, 0))
        finally:
            self.deck_combo.blockSignals(False)
        if picked and index < 0:
            # The picked deck is gone (renamed or deleted in Anki).
            self._on_deck_changed(0)

    def _on_deck_fetch_error(self, message: str) -> None:
        logger.warning("Anki Deck deck fetch failed: error=%s", message)
        self._on_decks_fetched([])

    # ------------------------------------------------------------------
    # Field inspection
    # ------------------------------------------------------------------

    def _selected_deck(self) -> str | None:
        return self.deck_combo.currentText() if self.deck_combo.currentIndex() > 0 else None

    def _on_deck_changed(self, _index: int) -> None:
        self._inspect_generation += 1
        self._note_count = None
        for combo in self._field_combos():
            while combo.count() > 1:
                combo.removeItem(combo.count() - 1)
            combo.setCurrentIndex(0)
        self._set_field_combos_enabled(False)
        self.fields_widget.hide()
        self._recompute_buttons()
        deck = self._selected_deck()
        if deck is None:
            self.status_label.setText("")
            return
        try:
            service = AnkiService(self.config)
        except ValueError as exc:
            logger.warning("Anki Deck inspect skipped: missing=field_mapping error=%s", exc)
            return
        generation = self._inspect_generation
        contains_target_script = get_profile(config_language(self.config)).script.contains_target_script
        self.status_label.setText(self.tr("Reading the deck…"))

        def work() -> tuple[DeckInspection, DeckFieldMap]:
            inspection = inspect_deck(service, deck)
            suggestion = suggest_field_map(
                inspection.field_names, inspection.samples, contains_target_script=contains_target_script
            )
            return inspection, suggestion

        run_off_thread(
            self,
            work,
            lambda answer: self._on_inspected(generation, answer),
            lambda message: self._on_inspect_error(generation, message),
            error_prefix=self.tr("Couldn't read the deck: "),
        )

    def _on_inspected(self, generation: int, answer: object) -> None:
        if generation != self._inspect_generation or not isinstance(answer, tuple):
            return
        inspection, suggestion = answer
        for combo in self._field_combos():
            combo.addItems(list(inspection.field_names))
        picks = (suggestion.sentence, suggestion.audio, suggestion.picture, suggestion.translation)
        for combo, pick in zip(self._field_combos(), picks, strict=True):
            index = combo.findText(pick, Qt.MatchFlag.MatchExactly) if pick else -1
            combo.setCurrentIndex(max(index, 0))
        self._set_field_combos_enabled(bool(inspection.field_names))
        self.fields_widget.setVisible(bool(inspection.field_names))
        self._note_count = inspection.note_count
        if inspection.note_count == 0:
            self.status_label.setText(self.tr("The selected deck has no notes."))
        else:
            self.status_label.setText(self.tr("%n note(s) in the deck.", "", inspection.note_count))
        self._recompute_buttons()

    def _on_inspect_error(self, generation: int, message: str) -> None:
        if generation != self._inspect_generation:
            return
        logger.warning("Anki Deck inspect failed: error=%s", message)
        self.status_label.setText(message)

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def _picked_fields(self) -> DeckFieldMap | None:
        """The field map the combos describe; None without a sentence field."""
        sentence, audio, picture, translation = (
            combo.currentText() if combo.currentIndex() > 0 else "" for combo in self._field_combos()
        )
        if not sentence:
            return None
        return DeckFieldMap(sentence=sentence, audio=audio, picture=picture, translation=translation)

    def _on_mine_clicked(self) -> None:
        """Mine the picked deck as one ephemeral queue item."""
        if self.worker_thread is not None:
            return
        # A fresh attempt supersedes the last complaint (after the reentrancy guard).
        self.clear_screen_issue()
        if self._deck_fetch_failed and self._deck_fetch_issue is not None:
            # The picker is off because Anki gave no deck list; that banner is
            # the answer, and the next good fetch takes it down (same object).
            self.show_screen_issue(self._deck_fetch_issue)
            return
        deck = self._selected_deck()
        fields = self._picked_fields()
        # An empty deck is named before the field check: with no notes, the
        # inspection finds no fields, so "pick its sentence field" would mislead.
        if deck is not None and self._note_count == 0:
            self._report_refusal(self.tr("The selected deck has no notes."))
            return
        if deck is None or fields is None:
            self._report_refusal(self.tr("Pick a deck and its sentence field first."))
            return
        # Phase 3' copies deck media only into a mapped field; say so now rather
        # than let the run drop it quietly.
        if fields.audio and not self.config.anki_fields.get("audio"):
            self.log_widget.append_warning(
                self.tr(
                    "No sentence-audio field is mapped in Settings → Cards & Anki, "
                    "so the deck's audio won't be copied."
                )
            )
        if fields.picture and not self.config.anki_fields.get("picture"):
            self.log_widget.append_warning(
                self.tr(
                    "No Picture field is mapped in Settings → Cards & Anki, so the deck's pictures won't be copied."
                )
            )
        ref = ReadingSourceRef(kind="deck", title=deck, deck_fields=fields)
        item = ReadingQueueItem(source=ref, title=deck, kind=ref.kind)
        if self._launch_run([item]):
            self._begin_progress()

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
        item = self._item_at(idx)
        if item is None:
            return
        status = tr_format(self.tr("Mining %1…"), item.title)
        self.overall_progress_widget.set_status(status)
        self._publish_reading_status(status)

    def _on_item_progress(self, idx: int, label: str) -> None:
        """Say what the run is doing. The bar counts finished items only."""
        if label:
            self.overall_progress_widget.set_status(label)
            self._publish_reading_status(label)

    def _on_item_finished(self, idx: int, result: object, error: object, attempts: int) -> None:
        """Log the outcome and forward a success result to the presenter.

        READ-ONLY: the worker has already recorded ``status``/``cards_created``/
        ``error_message`` on the item before emitting this signal.
        """
        if self._item_at(idx) is None:
            return
        outcome = self._record_item_outcome(result, error)
        if outcome is MiningOutcome.SUCCESS:
            cards = int(getattr(result, "cards_created", 0) or 0)
            self.log_widget.append_success(tr_format(self.tr("Mined %1 cards."), cards))
            if self._presenter is not None:
                # Best-effort: the worker has already recorded the result, so a
                # broken presenter slot must not take down the run -- but it is
                # logged, never swallowed.
                try:
                    self._presenter.show_processing_result(result)  # type: ignore[arg-type]
                except Exception:
                    logger.exception("Anki Deck: presenter result failed")
        elif outcome is MiningOutcome.CANCELLED:
            self.log_widget.append_info(self.tr("Cancelled."))
        else:
            message = str(error) if error is not None else result_error_text(result)
            self.log_widget.append_error(tr_format(self.tr("Failed: %1."), message))

        done = sum(1 for i in self._run_items if i.status in (ReadyItemStatus.COMPLETED, ReadyItemStatus.ERROR))
        self.overall_progress_widget.set_composed(done, len(self._run_items))
        self._publish_reading_done(done)

    def _on_queue_finished(self) -> None:
        """Single-item runs are already logged by ``_on_item_finished``."""

    def _after_run_cleanup(self) -> None:
        """Per-tab UI recovery after a run ends (every exit path)."""
        self.cancel_button.setText(self.tr("Cancel"))
        self.cancel_button.setEnabled(True)
        self._apply_terminal_bar_state(self.overall_progress_widget)
        self._recompute_buttons()

    def _recompute_buttons(self) -> None:
        """Refresh button state from the worker handle.

        Idle shows Mine, always enabled: a missing deck or sentence field, or an
        empty deck, is refused in the banner when Mine is pressed (A04). A run
        swaps Mine for Cancel.
        """
        # Built before the buttons exist: the sentence combo's first signal can
        # fire during construction.
        if not hasattr(self, "mine_button"):
            return
        run_active = self.worker_thread is not None
        self.mine_button.setVisible(not run_active)
        self.mine_button.setEnabled(not run_active)  # A04: a refusal explains itself
        self.cancel_button.setVisible(run_active)

    # ------------------------------------------------------------------
    # Curation context: the card's own picture + clip
    # ------------------------------------------------------------------

    def _build_curation_context(self):
        """Picture-and-clip curation context for the in-flight deck.

        Runs off the GUI thread (run_off_thread in the base) while the worker
        is parked in the curation wait, so its published ``curation_document``
        is stable. The dialog maps a word to its card via ``int(start_time)``,
        the same mapping phase 3' uses. A deck with neither pictures nor clips
        keeps the table-only context; the definition pane is wired either way.
        """
        worker = self.worker_thread
        proc = worker.curation_processor if worker is not None else None
        lookup = self._lookup_fn_from_processor(proc)
        doc = worker.curation_document if worker is not None else None
        if doc is None or doc.kind != "deck" or not any(u.image_ref or u.audio_ref for u in doc.units):
            return None, lookup
        # The caption under the picture is the card's own line, as on the deck's
        # card; the note ordinal stays in the Position column and the Source field.
        units = {u.index: dataclasses.replace(u, location_label=u.text) for u in doc.units}
        return CurationMediaContext(video_file=None, subtitle_entries=[], page_units=units), lookup

    # ------------------------------------------------------------------
    # Drops: nothing to take here -- the deck is picked above (D50)
    # ------------------------------------------------------------------

    def _drop_refusal(self) -> str:
        return self.tr("This screen mines a deck already in Anki. Pick it from the Deck list above.")

    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:  # noqa: N802 - Qt override
        """Accept a file drag only to say why it cannot land here."""
        if event is None or self.worker_thread is not None or not urls_from_event(event):
            return
        event.acceptProposedAction()
        self.status_label.setText(self._drop_refusal())

    def dragLeaveEvent(self, event: QDragLeaveEvent | None) -> None:  # noqa: N802 - Qt override
        """Take the refusal back down when the drag moves off the screen."""
        if self.status_label.text() == self._drop_refusal():
            self.status_label.setText("")
        if event is not None:
            event.accept()

    def dropEvent(self, event: QDropEvent | None) -> None:  # noqa: N802 - Qt override
        """Refuse the payload and point at the deck picker."""
        if event is None:
            return
        if self.worker_thread is None:
            self.status_label.setText(self._drop_refusal())
            self.deck_combo.setFocus(Qt.FocusReason.OtherFocusReason)
        event.ignore()
