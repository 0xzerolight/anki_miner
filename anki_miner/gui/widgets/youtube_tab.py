"""YouTube mining tab for the GUI.

Drives a multi-URL queue: the user pastes URLs, each one is probed
asynchronously, and once at least one item is READY the user can run
*Mine* across the whole queue. The tab itself is a thin shell
around three collaborators:

* :class:`~anki_miner.gui.widgets.youtube_playlist_flow.PlaylistAddController`
  — owns the Add flow: input gating (T-34), the parallel single-video probe
  workers, and the playlist resolve/confirm/expand detour (Issue #70).
* :class:`~anki_miner.gui.workers.youtube_queue_worker.YouTubeQueueWorker` —
  single long-running worker that sweeps the queue sequentially.
* :class:`~anki_miner.gui.widgets.youtube_queue_item_widget.YouTubeQueueItemWidget` —
  per-row renderer embedded inside a :class:`QListWidget`.

The queue-list lifecycle — Mine/Clear/Stop, the per-item signal slots, the
terminal-bar summary, worker/processor management, and curation — is shared with
:class:`~anki_miner.gui.widgets.audiobook_tab.AudiobookTab` on
:class:`~anki_miner.gui.widgets._queue_mining_tab_base._ListQueueMiningTabBase`
(ARC-008). This tab adds only the URL/probe/playlist Add flow, the fetcher
rebuild, and the per-tab adapters (worker class, row widget, item labels, status
enum, the Add lock while a playlist resolves).

Button enable/disable is recomputed on every queue/worker signal by
:meth:`_recompute_buttons` (base). There is no explicit state enum — the queue
contents plus the worker handle (and the add-flow controller's ``is_active``)
fully determine the UI.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.service_factory import create_episode_processor, create_youtube_fetcher
from anki_miner.gui.widgets._queue_mining_tab_base import (
    _ListQueueMiningTabBase,
    _QueueListStrings,
    _QueueRunStrings,
)
from anki_miner.gui.widgets.enhanced import ModernButton, SectionHeader
from anki_miner.gui.widgets.log_widget import LogWidget
from anki_miner.gui.widgets.progress_widget import ProgressWidget
from anki_miner.gui.widgets.youtube_playlist_flow import PlaylistAddCallbacks, PlaylistAddController
from anki_miner.gui.widgets.youtube_queue_item_widget import YouTubeQueueItemWidget
from anki_miner.gui.workers.youtube_queue_worker import YouTubeQueueWorker
from anki_miner.interfaces.presenter import PresenterProtocol
from anki_miner.models.youtube_queue import YouTubeItemStatus, YouTubeQueue, YouTubeQueueItem
from anki_miner.orchestration import EpisodeProcessor
from anki_miner.services.youtube_fetcher import YouTubeFetcherService

if TYPE_CHECKING:
    from anki_miner.gui.workers._queue_worker_base import SequentialQueueWorker

logger = logging.getLogger(__name__)


class YouTubeTab(_ListQueueMiningTabBase):
    """Multi-URL YouTube queue mining tab.

    The tab owns a :class:`YouTubeQueue`, a :class:`PlaylistAddController`
    holding the in-flight probe/playlist workers, and (via the base) at most one
    running :class:`YouTubeQueueWorker`. Button state is derived from the queue
    contents and the worker handle via :meth:`_recompute_buttons` (base).
    """

    _shutdown_log_name = "YouTube"
    _status_ready = YouTubeItemStatus.READY
    _status_processing = YouTubeItemStatus.PROCESSING
    _status_completed = YouTubeItemStatus.COMPLETED
    _status_error = YouTubeItemStatus.ERROR

    def __init__(
        self,
        config: AnkiMinerConfig,
        processor: EpisodeProcessor | None,
        fetcher: YouTubeFetcherService,
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
            fetcher: YouTube fetcher service used for metadata probes and,
                indirectly via ``processor.process_youtube_url``, downloads.
            presenter: Optional presenter for routing log messages.
            parent: Optional parent widget.
            stats_service: Optional ``StatsService`` reused across lazy processor
                rebuilds so YouTube mining sessions land in analytics.
        """
        super().__init__(config, processor, presenter, parent, stats_service)
        self._fetcher = fetcher

        # Queue model + per-row widget map.
        self._queue: YouTubeQueue = YouTubeQueue()
        self._row_widgets: dict[YouTubeQueueItem, YouTubeQueueItemWidget] = {}
        self._list_items: dict[YouTubeQueueItem, QListWidgetItem] = {}

        # Launch-banner + queue-list strings, kept in this tab's tr-context (see
        # the i18n note in _queue_mining_tab_base). Built once at construction
        # like _ToolTabBase's _ToolTabStrings. The mined/failed_item templates
        # carry an attempts=%3 suffix unique to YouTube.
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
            mined=self.tr("Mined %1: %2 cards (attempts=%3)."),
            cancelled_item=self.tr("Cancelled %1."),
            failed_item=self.tr("Failed %1: %2 (attempts=%3)."),
            cancelled=self.tr("Cancelled"),
            failed_see_log=self.tr("Failed — see log"),
            complete_succeeded=self.tr("Complete — %1 succeeded"),
            complete_with_failures=self.tr("Complete — %1 succeeded, %2 failed"),
        )

        self._setup_ui()

        # Add-flow controller: probe workers, playlist resolve/expand, choice
        # dialog (Issue #70). Constructed after _setup_ui so the widget-bound
        # callbacks (url_edit, log_widget) exist; the tab stays the Qt parent of
        # every spawned worker thread.
        self._add_flow = PlaylistAddController(
            fetcher=fetcher,
            config=config,
            callbacks=PlaylistAddCallbacks(
                enqueue=self._queue.add,
                queued_items=self._queue.all_items,
                render_new_item=self._render_new_item,
                refresh_row=self._refresh_row,
                recompute_buttons=self._recompute_buttons,
                clear_url_input=self.url_edit.clear,
                log_info=self.log_widget.append_info,
                log_warning=self.log_widget.append_warning,
                log_error=self.log_widget.append_error,
            ),
            parent=self,
        )

        self._recompute_buttons()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """Build the tab layout.

        A QScrollArea wraps a Queue card (URL input + list + action buttons),
        a Progress card, and a LogWidget.
        """
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        # --- Queue card: URL row + list + action buttons
        queue_card = QFrame()
        queue_card.setObjectName("card")
        queue_layout = QVBoxLayout()
        queue_layout.setSpacing(SPACING.sm)
        queue_layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        queue_layout.addWidget(SectionHeader(self.tr("YouTube queue")))

        # URL row
        url_row = QHBoxLayout()
        url_row.setSpacing(SPACING.xs)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://www.youtube.com/watch?v=…")
        self.url_edit.returnPressed.connect(self._on_add_clicked)
        url_row.addWidget(self.url_edit, 1)

        self.add_button = ModernButton(self.tr("Add"), variant="secondary")
        self.add_button.setToolTip(self.tr("Add the URL to the queue and probe its metadata."))
        self.add_button.clicked.connect(self._on_add_clicked)
        url_row.addWidget(self.add_button)
        queue_layout.addLayout(url_row)

        # Queue list
        self.list_widget = QListWidget()
        self.list_widget.setObjectName("yt-queue-list")
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.list_widget.setUniformItemSizes(False)
        queue_layout.addWidget(self.list_widget, 1)

        # Empty-state hint (shown when the list is empty).
        self.empty_label = QLabel(self.tr("Paste a YouTube URL above and click Add."))
        self.empty_label.setObjectName("helper-text")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        queue_layout.addWidget(self.empty_label)

        # Issue #65: opt-in per-video word curation popup (default off).
        self.review_words_checkbox = QCheckBox(self.tr("Review words before mining"))
        self.review_words_checkbox.setChecked(False)
        self.review_words_checkbox.setToolTip(
            self.tr("Show the word-selection popup for each video before creating cards.")
        )
        queue_layout.addWidget(self.review_words_checkbox)

        # Action buttons
        button_row = QHBoxLayout()
        button_row.setSpacing(SPACING.xs)

        self.mine_button = ModernButton(self.tr("Mine"), variant="primary")
        self.mine_button.setToolTip(self.tr("Mine every READY item in the queue into Anki cards."))
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
    # Add flow (delegated to PlaylistAddController)
    # ------------------------------------------------------------------

    def _on_add_clicked(self) -> None:
        """Hand the current URL to the add-flow controller."""
        if not self.add_button.isEnabled():
            return  # Defensive: returnPressed fires even when the button is disabled.
        url = self.url_edit.text().strip()
        if not url:
            return
        self._add_flow.begin(url)

    # ------------------------------------------------------------------
    # Per-tab adapters for the shared list-queue lifecycle
    # ------------------------------------------------------------------

    def _make_worker(
        self,
        items: list[Any],
        curation_callback: Callable[[list], list | None] | None,
        processor_factory: Callable[[], EpisodeProcessor] | None,
    ) -> SequentialQueueWorker[Any]:
        """Construct the YouTube queue worker (name resolves here for tests)."""
        return YouTubeQueueWorker(
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

    def _make_row_widget(self, item: YouTubeQueueItem) -> YouTubeQueueItemWidget:
        """Construct the per-row queue widget for ``item``."""
        return YouTubeQueueItemWidget(item)

    def _item_started_label(self, item: YouTubeQueueItem) -> str:
        """Progress label for the ``Mining N of M`` line (title, or the URL)."""
        return item.video_info.title if item.video_info else item.url

    def _item_finished_label(self, item: YouTubeQueueItem) -> str:
        """Log label for the per-item finish line."""
        return item.url

    def _add_locked(self) -> bool:
        """Also lock Add while a playlist resolve is pending (a second Add
        mid-resolve would race the confirmation dialog)."""
        return self._add_flow.is_active

    def _on_clear_extra(self) -> None:
        """Invalidate pending playlist work (late-resolve generation bump +
        entry-probe cancel) — that state lives on the add-flow controller."""
        self._add_flow.invalidate_pending()

    # ------------------------------------------------------------------
    # Lifecycle overrides (fetcher + add-flow teardown)
    # ------------------------------------------------------------------

    def update_config(self, config: AnkiMinerConfig) -> None:
        """Adopt a new frozen config: rebuild the fetcher, then run the base
        processor lazy-drop/dirty reconciliation.

        The fetcher is cheap (snapshots config in the ctor) and always rebuilt;
        the new snapshot is pushed into the add-flow controller so future probes
        classify against the updated limits (in-flight workers captured the old
        fetcher at construction and are unaffected). The processor lazy-drop /
        _config_dirty handling is inherited from the base (OVH-014/OVH-056).
        """
        self._fetcher = create_youtube_fetcher(config)
        self._add_flow.update_config(config, self._fetcher)
        super().update_config(config)

    def shutdown(self) -> None:
        """Stop the active worker (base), then tear down the probe/playlist workers.

        The probe/playlist workers are owned by the add-flow controller, which
        gets its own shutdown call after the queue worker is joined — same
        teardown order as before the extraction.
        """
        super().shutdown()
        self._add_flow.shutdown()
