"""Audiobook mining tab for the GUI (Issue #71).

Drives a multi-pair queue: the user picks an audio file and a matching
subtitle file, Add validates and queues the pair, and once at least one
item is READY the user can run *Mine* across the whole queue.

The queue-list lifecycle — Mine/Clear/Stop, the per-item signal slots, the
terminal-bar summary, worker/processor management, curation, and the D28
selection/filter/search/reorder surface — is shared with
:class:`~anki_miner.gui.widgets.youtube_tab.YouTubeTab` on
:class:`~anki_miner.gui.widgets._queue_mining_tab_base._ListQueueMiningTabBase`
(ARC-008). This tab supplies only the local file-pair Add flow (local pairs
need no probe stage, so items enter the queue READY), its own layout, and the
per-tab adapters (worker class, row widget, item labels, status enum, filter
bucket, search text).

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
from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils import queue_state_store
from anki_miner.gui.utils.queue_state_store import QueueItemSnapshot, QueueSnapshot
from anki_miner.gui.utils.service_factory import create_episode_processor
from anki_miner.gui.widgets._queue_mining_tab_base import (
    _ListQueueMiningTabBase,
    _QueueListStrings,
    _QueueRunStrings,
)
from anki_miner.gui.widgets.audiobook_queue_item_widget import (
    AudiobookQueueItemWidget,
    queue_bucket,
)
from anki_miner.gui.widgets.base import (
    PageWidth,
    configure_card_layout,
    field_label_width,
    page_filler,
)
from anki_miner.gui.widgets.current_job_strip import CurrentJobStrip
from anki_miner.gui.widgets.enhanced import FileSelector, ModernButton, SectionHeader, accepts_suffixes
from anki_miner.gui.widgets.log_widget import LogWidget
from anki_miner.gui.widgets.progress_widget import ProgressWidget
from anki_miner.gui.widgets.queue_controls_bar import QueueControlsBar
from anki_miner.gui.workers.audiobook_queue_worker import AudiobookQueueWorker
from anki_miner.interfaces.presenter import PresenterProtocol
from anki_miner.models.audiobook_queue import AudiobookQueue, AudiobookQueueItem
from anki_miner.models.mining_queue import ReadyItemStatus
from anki_miner.orchestration import EpisodeProcessor
from anki_miner.utils.i18n import tr_format
from anki_miner.utils.logging_ext import log_summary

if TYPE_CHECKING:
    from anki_miner.gui.workers._queue_worker_base import SequentialQueueWorker

logger = logging.getLogger(__name__)

# Subtitle extensions probed (in order) for the same-stem auto-fill.
_SUBTITLE_EXTS = (".srt", ".vtt", ".ass", ".ssa")
# Audio extensions this tab mines. One list per kind: the Browse filter and the
# drag-and-drop validator (D50) are both derived from it, so a format can never
# be pickable and undroppable at the same time.
_AUDIO_EXTS = (".m4b", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".wav")


def _file_filter(label: str, extensions: tuple[str, ...]) -> str:
    """Render a Qt file-dialog filter from one extension list."""
    return f"{label} ({' '.join('*' + extension for extension in extensions)})"


_AUDIO_FILTER = _file_filter("Audio Files", _AUDIO_EXTS)
_SUBTITLE_FILTER = _file_filter("Subtitle Files", _SUBTITLE_EXTS)


class AudiobookTab(_ListQueueMiningTabBase):
    """Multi-pair audiobook queue mining tab.

    The tab owns an :class:`AudiobookQueue` and, via the base, at most one
    running :class:`AudiobookQueueWorker`. Button state is derived from the queue
    contents and the worker handle via :meth:`_recompute_buttons` (base).
    """

    #: Tables and queue rows genuinely use the extra width.
    PAGE_WIDTH = PageWidth.PAGE

    _shutdown_log_name = "Audiobook"
    _status_ready = ReadyItemStatus.READY
    _status_processing = ReadyItemStatus.PROCESSING
    _status_completed = ReadyItemStatus.COMPLETED
    _status_error = ReadyItemStatus.ERROR

    TASK_ID = "queue.audiobook"
    TASK_OWNER = CapabilityTarget("audiobook")

    #: Stable filename for this queue's recovery snapshot (D16-C).
    QUEUE_STATE_KEY = "queue.audiobook"

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
        self._last_auto_filled_subtitle: str | None = None

        # Launch-banner + queue-list strings, kept in this tab's tr-context (see
        # the i18n note in _queue_mining_tab_base). Built once at construction
        # like _ToolTabBase's _ToolTabStrings.
        self._run_strings = _QueueRunStrings(
            unavailable=self.tr("Mining unavailable — services not initialized."),
            run_starting=self.tr("%1 run starting — %2 items."),
            mine_label=self.tr("Mine"),
            task_title=self.tr("Audio queue"),
            retrying=self.tr("Attempt %1 of %2 · retrying in %3s"),
        )
        self._queue_list_strings = _QueueListStrings(
            cancelling=self.tr("Cancelling…"),
            stop_all=self.tr("Cancel"),
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

        container = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        # --- Queue card: file pickers + Add + list + action buttons
        queue_card = QFrame()
        queue_card.setObjectName("card")
        queue_layout = QVBoxLayout()
        configure_card_layout(queue_layout)

        queue_layout.addWidget(SectionHeader(self.tr("Audio queue")))

        # Derive the shared column from the TRANSLATED labels rather than a
        # hardcoded 100: German "Untertiteldatei:" needed 149px in that 84px box.
        label_w = field_label_width(self.tr("Audio File:"), self.tr("Subtitle File:"))

        self.audio_selector = FileSelector(
            label=self.tr("Audio File:"),
            file_filter=_AUDIO_FILTER,
            label_width=label_w,
            history_key="audio.inputs",
            drop_validator=accepts_suffixes(_AUDIO_EXTS, self.tr("This field takes an audio file.")),
        )
        self.audio_selector.path_changed.connect(self._on_audio_path_changed)
        queue_layout.addWidget(self.audio_selector)

        self.subtitle_selector = FileSelector(
            label=self.tr("Subtitle File:"),
            file_filter=_SUBTITLE_FILTER,
            label_width=label_w,
            history_key="audio.inputs",
            drop_validator=accepts_suffixes(_SUBTITLE_EXTS, self.tr("This field takes a subtitle file.")),
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

        # Filters, search, counter and the selection actions (D28).
        self.queue_controls = QueueControlsBar()
        queue_layout.addWidget(self.queue_controls)

        # The one line describing the item actually being mined (D31).
        self.current_job_strip = CurrentJobStrip()
        queue_layout.addWidget(self.current_job_strip)

        # Queue list
        self.list_widget = QListWidget()
        self.list_widget.setObjectName("audiobook-queue-list")
        self.list_widget.setUniformItemSizes(False)
        # No stretch factor: the list's own Expanding policy already takes the
        # card's surplus. A stretch here would keep the CARD expanding even
        # while the list is hidden on an empty queue -- QBoxLayout reports
        # itself expansive whenever any item carries stretch, hidden or not --
        # so the page could never hand that height back. Same call shape as
        # QueuePanel and the Reading file list.
        queue_layout.addWidget(self.list_widget)
        self._wire_queue_interaction()

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

        self.stop_button = ModernButton(self.tr("Cancel"), variant="secondary")
        self.stop_button.setToolTip(self.tr("Cancel the active run."))
        self.stop_button.clicked.connect(self._on_stop_all_clicked)

        # Clear acts on the list right above it and stays with it. Mine and
        # Cancel move to the pinned bar (D6).
        button_row.addWidget(self.clear_button)
        button_row.addStretch()
        queue_layout.addLayout(button_row)

        queue_card.setLayout(queue_layout)
        layout.addWidget(queue_card)

        # --- Progress card
        progress_card = QFrame()
        progress_card.setObjectName("card")
        progress_layout = QVBoxLayout()
        configure_card_layout(progress_layout)

        progress_layout.addWidget(SectionHeader(self.tr("Progress")))
        self.progress_widget = ProgressWidget()
        progress_layout.addWidget(self.progress_widget)
        # The durable end state of this same card (D20).
        self._install_receipt(progress_layout, self.progress_widget, item_noun=self.tr("audiobooks"))

        progress_card.setLayout(progress_layout)
        layout.addWidget(progress_card)

        # --- LogWidget: own header + Copy/Clear actions; install_workflow_shell moves it into the Activity drawer (D6).
        self.log_widget = LogWidget()

        # Stands in for the queue list while an empty queue keeps it hidden, so
        # the page's leftover height still pools below the cards instead of
        # inflating their headings. Toggled with the list in _recompute_buttons.
        self.page_filler = page_filler()
        layout.addWidget(self.page_filler)

        container.setLayout(layout)

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        self._install_action_bar(
            main_layout,
            scroll_area,
            container,
            self.PAGE_WIDTH,
            primary=self.mine_button,
            secondary=(self.stop_button,),
            log=self.log_widget,
        )
        self.setLayout(main_layout)

    # ------------------------------------------------------------------
    # Add flow
    # ------------------------------------------------------------------

    def _on_audio_path_changed(self, text: str) -> None:
        """Auto-fill the subtitle picker with the same-stem subtitle next to the audio file.

        Replaces this tab's prior auto-fill when the audio changes. A
        user-chosen subtitle is never overwritten.
        """
        current_subtitle = self.subtitle_selector.path_or_none()
        owns_subtitle = (
            self._last_auto_filled_subtitle is not None and current_subtitle == self._last_auto_filled_subtitle
        )
        if current_subtitle is not None and not owns_subtitle:
            self._last_auto_filled_subtitle = None
            return

        self._last_auto_filled_subtitle = None
        audio = Path(text) if text.strip() else None
        if audio is None or not audio.is_file():
            if owns_subtitle:
                self.subtitle_selector.clear()
            return
        for ext in _SUBTITLE_EXTS:
            candidate = audio.with_suffix(ext)
            if candidate.is_file():
                subtitle_path = str(candidate)
                self._last_auto_filled_subtitle = subtitle_path
                self.subtitle_selector.set_path(subtitle_path)
                return
        if owns_subtitle:
            self.subtitle_selector.clear()

    def _on_add_clicked(self) -> None:
        """Validate the picked pair and append it to the queue as a READY item."""
        if not self.add_button.isEnabled():
            return  # Defensive: out-of-band trigger while a run is active.
        audio_text = self.audio_selector.path_or_none()
        sub_text = self.subtitle_selector.path_or_none()
        if audio_text is None and sub_text is None:
            return
        if audio_text is None or not Path(audio_text).is_file():
            log_summary(
                logger,
                "Audiobook add rejected",
                level=logging.WARNING,
                reason="audio_file_missing",
                file=Path(audio_text) if audio_text else None,
            )
            self.log_widget.append_error(
                tr_format(self.tr("Audio file not found: %1"), audio_text or self.tr("(none selected)"))
            )
            return
        if sub_text is None or not Path(sub_text).is_file():
            log_summary(
                logger,
                "Audiobook add rejected",
                level=logging.WARNING,
                reason="subtitle_file_missing",
                file=Path(sub_text) if sub_text else None,
            )
            self.log_widget.append_error(
                tr_format(self.tr("Subtitle file not found: %1"), sub_text or self.tr("(none selected)"))
            )
            return

        item = self._queue.add(Path(audio_text), Path(sub_text))
        self._render_new_item(item)
        # Clearing audio removes only a subtitle still owned by auto-fill;
        # explicit subtitle clearing handles a user-chosen value.
        self.audio_selector.clear()
        self.subtitle_selector.clear()
        self._recompute_buttons()

    # ------------------------------------------------------------------
    # Durable queue contents (D16-C)
    # ------------------------------------------------------------------

    def queue_snapshot(self) -> QueueSnapshot:
        """Describe the queue in terms that survive quitting.

        A pair is two paths, a status and a count. Nothing here is a worker, a
        processor or a temporary file — the run that owned those is over.
        """
        return QueueSnapshot(
            key=self.QUEUE_STATE_KEY,
            items=tuple(
                QueueItemSnapshot(
                    item_id=item.item_id,
                    source=queue_state_store.file_pair_source(item.audio_file, item.subtitle_file),
                    title=item.audio_file.name,
                    status=queue_state_store.status_from_run_state(item.status.value),
                    error=item.error_message or "",
                    result_count=item.cards_created,
                )
                for item in self._queue.all_items()
            ),
        )

    def restore_queue_snapshot(self, snapshot: QueueSnapshot) -> int:
        """Rebuild the queue from ``snapshot`` in order; return the row count.

        Nothing starts: rows come back ready, completed or held, and a row that
        was mid-run comes back saying so. A row whose files have since moved
        comes back as a failure rather than as a row that would fail on Mine.
        """
        if self.worker_thread is not None or self._queue.all_items():
            return 0
        restored = 0
        for row in snapshot.items:
            source = row.source
            item = self._queue.add(Path(str(source["audio"])), Path(str(source["subtitle"])))
            item.item_id = row.item_id
            item.cards_created = row.result_count
            missing = row.missing_paths()
            if missing:
                item.status = ReadyItemStatus.ERROR
                item.error_message = tr_format(self.tr("File not found: %1"), str(missing[0]))
            elif row.is_interrupted:
                item.status = ReadyItemStatus.ERROR
                item.error_message = self.tr("Interrupted when Anki Miner closed")
            elif row.status == queue_state_store.STATUS_COMPLETED:
                item.status = ReadyItemStatus.COMPLETED
            elif row.status == queue_state_store.STATUS_ERROR:
                item.status = ReadyItemStatus.ERROR
                item.error_message = row.error
            self._render_new_item(item)
            restored += 1
        self._recompute_buttons()
        return restored

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

    def _filter_bucket(self, item: AudiobookQueueItem) -> str:
        """Map the item's status to a filter chip (shared with the row widget)."""
        return queue_bucket(item)

    def _search_text(self, item: AudiobookQueueItem) -> str:
        """Both file names are searchable — a pair is found by either half."""
        return f"{item.audio_file.name} {item.subtitle_file.name}"
