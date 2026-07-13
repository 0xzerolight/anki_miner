"""Audiobook mining tab for the GUI (Issue #71).

Drives a multi-pair queue: the user picks an audio file and a matching
subtitle file, Add validates and queues the pair, and once at least one
item is READY the user can run *Mine* across the whole queue.

The queue-list lifecycle — Mine/Clear/Stop, the per-item signal slots, the
terminal-bar summary, worker/processor management, and curation — is shared
with :class:`~anki_miner.gui.widgets.youtube_tab.YouTubeTab` on
:class:`~anki_miner.gui.widgets._queue_mining_tab_base._ListQueueMiningTabBase`
(ARC-008). This tab supplies only the local file-pair Add flow (local pairs
need no probe stage, so items enter the queue READY), its own layout, and the
per-tab adapters (worker class, row widget, item labels, status enum).

Two collaborators:

* :class:`~anki_miner.gui.workers.audiobook_queue_worker.AudiobookQueueWorker`
  — single long-running worker that sweeps the queue sequentially.
* :class:`~anki_miner.gui.widgets.audiobook_queue_item_widget.AudiobookQueueItemWidget`
  — per-row renderer embedded inside a :class:`QListWidget`.

Button enable/disable is recomputed on every queue/worker signal by
:meth:`_recompute_buttons` (base). There is no explicit state enum — the queue
contents plus the worker handle fully determine the UI.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.service_factory import create_episode_processor
from anki_miner.gui.widgets._queue_mining_tab_base import (
    _ListQueueMiningTabBase,
    _QueueListStrings,
    _QueueRunStrings,
)
from anki_miner.gui.widgets.audiobook_queue_item_widget import AudiobookQueueItemWidget
from anki_miner.gui.widgets.enhanced import FileSelector, ModernButton, SectionHeader
from anki_miner.gui.widgets.log_widget import LogWidget
from anki_miner.gui.widgets.progress_widget import ProgressWidget
from anki_miner.gui.workers.audiobook_queue_worker import AudiobookQueueWorker
from anki_miner.interfaces.presenter import PresenterProtocol
from anki_miner.models.audiobook_queue import AudiobookItemStatus, AudiobookQueue, AudiobookQueueItem
from anki_miner.orchestration import EpisodeProcessor
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from anki_miner.gui.workers._queue_worker_base import SequentialQueueWorker

logger = logging.getLogger(__name__)

# Subtitle extensions probed (in order) for the same-stem auto-fill.
_SUBTITLE_EXTS = (".srt", ".vtt", ".ass", ".ssa")

_AUDIO_FILTER = "Audio Files (*.m4b *.mp3 *.m4a *.aac *.ogg *.opus *.flac *.wav)"
_SUBTITLE_FILTER = "Subtitle Files (*.srt *.vtt *.ass *.ssa)"


class AudiobookTab(_ListQueueMiningTabBase):
    """Multi-pair audiobook queue mining tab.

    The tab owns an :class:`AudiobookQueue` and, via the base, at most one
    running :class:`AudiobookQueueWorker`. Button state is derived from the queue
    contents and the worker handle via :meth:`_recompute_buttons` (base).
    """

    _shutdown_log_name = "Audiobook"
    _status_ready = AudiobookItemStatus.READY
    _status_processing = AudiobookItemStatus.PROCESSING
    _status_completed = AudiobookItemStatus.COMPLETED
    _status_error = AudiobookItemStatus.ERROR

    def __init__(
        self,
        config: AnkiMinerConfig,
        processor: EpisodeProcessor | None,
        presenter: PresenterProtocol | None = None,
        parent: QWidget | None = None,
        stats_service: object | None = None,
    ) -> None:
        """Initialize the tab.

        Args:
            config: Frozen application configuration.
            processor: Episode processor (reused across runs within this tab).
                May be ``None`` so the tab can be constructed before the
                dictionary chain has loaded; the first run builds one lazily.
            presenter: Optional presenter for routing log messages.
            parent: Optional parent widget.
            stats_service: Optional ``StatsService`` reused across lazy processor
                rebuilds so audiobook mining sessions land in analytics.
        """
        super().__init__(config, processor, presenter, parent, stats_service)

        # Queue model + per-row widget map.
        self._queue: AudiobookQueue = AudiobookQueue()
        self._row_widgets: dict[AudiobookQueueItem, AudiobookQueueItemWidget] = {}
        self._list_items: dict[AudiobookQueueItem, QListWidgetItem] = {}

        # Launch-banner + queue-list strings, kept in this tab's tr-context (see
        # the i18n note in _queue_mining_tab_base). Built once at construction
        # like _ToolTabBase's _ToolTabStrings.
        self._run_strings = _QueueRunStrings(
            unavailable=self.tr("Mining unavailable — services not initialized."),
            run_starting=self.tr("%1 run starting — %2 items."),
            mine_label=self.tr("Mine"),
        )
        self._queue_list_strings = _QueueListStrings(
            cancelling=self.tr("Cancelling…"),
            stop_all=self.tr("Stop All"),
            queue_done=self.tr("Queue done: %1 succeeded, %2 failed."),
            mining_n_of_m=self.tr("Mining %1 of %2: %3"),
            mined=self.tr("Mined %1: %2 cards."),
            cancelled_item=self.tr("Cancelled %1."),
            failed_item=self.tr("Failed %1: %2."),
            cancelled=self.tr("Cancelled"),
            failed_see_log=self.tr("Failed — see log"),
            complete_succeeded=self.tr("Complete — %1 succeeded"),
            complete_with_failures=self.tr("Complete — %1 succeeded, %2 failed"),
        )

        self._setup_ui()
        self._recompute_buttons()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """Build the tab layout.

        A QScrollArea wraps a Queue card (file pickers + list + action
        buttons), a Progress card, and a LogWidget.
        """
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        # --- Queue card: file pickers + Add + list + action buttons
        queue_card = QFrame()
        queue_card.setObjectName("card")
        queue_layout = QVBoxLayout()
        queue_layout.setSpacing(SPACING.sm)
        queue_layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        queue_layout.addWidget(SectionHeader(self.tr("Audio queue")))

        self.audio_selector = FileSelector(
            label=self.tr("Audio File:"),
            file_filter=_AUDIO_FILTER,
            label_width=100,
        )
        self.audio_selector.path_changed.connect(self._on_audio_path_changed)
        queue_layout.addWidget(self.audio_selector)

        self.subtitle_selector = FileSelector(
            label=self.tr("Subtitle File:"),
            file_filter=_SUBTITLE_FILTER,
            label_width=100,
        )
        queue_layout.addWidget(self.subtitle_selector)

        add_row = QHBoxLayout()
        add_row.setSpacing(SPACING.xs)
        self.add_button = ModernButton(self.tr("Add"), variant="secondary")
        self.add_button.setToolTip(self.tr("Add the audio + subtitle pair to the queue."))
        self.add_button.clicked.connect(self._on_add_clicked)
        add_row.addWidget(self.add_button)
        add_row.addStretch()
        queue_layout.addLayout(add_row)

        # Queue list
        self.list_widget = QListWidget()
        self.list_widget.setObjectName("audiobook-queue-list")
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.list_widget.setUniformItemSizes(False)
        queue_layout.addWidget(self.list_widget, 1)

        # Empty-state hint (shown when the list is empty).
        self.empty_label = QLabel(self.tr("Pick an audio file and its subtitle above, then click Add."))
        self.empty_label.setObjectName("helper-text")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        queue_layout.addWidget(self.empty_label)

        # Issue #65: opt-in per-item word curation popup (default off).
        self.review_words_checkbox = QCheckBox(self.tr("Review words before mining"))
        self.review_words_checkbox.setChecked(False)
        self.review_words_checkbox.setToolTip(
            self.tr("Show the word-selection popup for each audio file before creating cards.")
        )
        queue_layout.addWidget(self.review_words_checkbox)

        # Action buttons
        button_row = QHBoxLayout()
        button_row.setSpacing(SPACING.xs)

        self.mine_button = ModernButton(self.tr("Mine"), variant="primary")
        self.mine_button.setToolTip(self.tr("Mine every queued item into Anki cards."))
        self.mine_button.clicked.connect(self._on_mine_clicked)

        self.clear_button = ModernButton(self.tr("Clear"), variant="ghost")
        self.clear_button.setToolTip(self.tr("Remove every queued item that is not currently mining."))
        self.clear_button.clicked.connect(self._on_clear_clicked)

        self.stop_button = ModernButton(self.tr("Stop All"), variant="danger")
        self.stop_button.setToolTip(self.tr("Cancel the active run."))
        self.stop_button.clicked.connect(self._on_stop_all_clicked)

        button_row.addWidget(self.mine_button)
        button_row.addWidget(self.clear_button)
        button_row.addWidget(self.stop_button)
        button_row.addStretch()
        queue_layout.addLayout(button_row)

        queue_card.setLayout(queue_layout)
        layout.addWidget(queue_card)

        # --- Progress card
        progress_card = QFrame()
        progress_card.setObjectName("card")
        progress_layout = QVBoxLayout()
        progress_layout.setSpacing(SPACING.sm)
        progress_layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        progress_layout.addWidget(SectionHeader(self.tr("Progress")))
        self.progress_widget = ProgressWidget()
        progress_layout.addWidget(self.progress_widget)

        progress_card.setLayout(progress_layout)
        layout.addWidget(progress_card)

        # --- LogWidget (carries its own header + Copy/Clear actions)
        self.log_widget = LogWidget()
        layout.addWidget(self.log_widget)

        container.setLayout(layout)
        scroll_area.setWidget(container)

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll_area)
        self.setLayout(main_layout)

    # ------------------------------------------------------------------
    # Add flow
    # ------------------------------------------------------------------

    def _on_audio_path_changed(self, text: str) -> None:
        """Auto-fill the subtitle picker with the same-stem subtitle next to the audio file.

        Fills ONLY when the subtitle field is currently empty — a user-chosen
        subtitle is never overwritten.
        """
        if self.subtitle_selector.get_path().strip():
            return
        audio = Path(text) if text.strip() else None
        if audio is None or not audio.is_file():
            return
        for ext in _SUBTITLE_EXTS:
            candidate = audio.with_suffix(ext)
            if candidate.is_file():
                self.subtitle_selector.set_path(str(candidate))
                return

    def _on_add_clicked(self) -> None:
        """Validate the picked pair and append it to the queue as a READY item."""
        if not self.add_button.isEnabled():
            return  # Defensive: out-of-band trigger while a run is active.
        audio_text = self.audio_selector.path_or_none()
        sub_text = self.subtitle_selector.path_or_none()
        if audio_text is None and sub_text is None:
            return
        if audio_text is None or not Path(audio_text).is_file():
            self.log_widget.append_error(
                tr_format(self.tr("Audio file not found: %1"), audio_text or self.tr("(none selected)"))
            )
            return
        if sub_text is None or not Path(sub_text).is_file():
            self.log_widget.append_error(
                tr_format(self.tr("Subtitle file not found: %1"), sub_text or self.tr("(none selected)"))
            )
            return

        item = self._queue.add(Path(audio_text), Path(sub_text))
        self._render_new_item(item)
        # Clearing is order-independent: _on_audio_path_changed bails on empty
        # text, so the pickers can be cleared in any order without the auto-fill
        # re-triggering.
        self.audio_selector.clear()
        self.subtitle_selector.clear()
        self._recompute_buttons()

    # ------------------------------------------------------------------
    # Per-tab adapters for the shared list-queue lifecycle
    # ------------------------------------------------------------------

    def _make_worker(
        self,
        items: list[Any],
        curation_callback: Callable[[list], list | None] | None,
        processor_factory: Callable[[], EpisodeProcessor] | None,
    ) -> SequentialQueueWorker[Any]:
        """Construct the audiobook queue worker (name resolves here for tests)."""
        return AudiobookQueueWorker(
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

    def _make_row_widget(self, item: AudiobookQueueItem) -> AudiobookQueueItemWidget:
        """Construct the per-row queue widget for ``item``."""
        return AudiobookQueueItemWidget(item)

    def _item_started_label(self, item: AudiobookQueueItem) -> str:
        """Progress label for the ``Mining N of M`` line."""
        return item.audio_file.name

    def _item_finished_label(self, item: AudiobookQueueItem) -> str:
        """Log label for the per-item finish line."""
        return item.audio_file.name
