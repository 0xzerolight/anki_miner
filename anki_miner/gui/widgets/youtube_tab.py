"""YouTube mining tab for the GUI.

Drives a multi-URL queue: the user pastes links into a box, one per line, and
presses *Mine*. Mine queues every line, waits until every video has been probed,
then runs across every READY row. The tab itself is a thin shell around three
collaborators:

* :class:`~anki_miner.gui.widgets.youtube_playlist_flow.PlaylistAddController`
  — owns the add flow: input gating (T-34), dedup, the capped single-video
  probe workers, and the playlist resolve/confirm/expand detour (Issue #70).
* :class:`~anki_miner.gui.workers.youtube_queue_worker.YouTubeQueueWorker` —
  single long-running worker that sweeps the queue sequentially.
* :class:`~anki_miner.gui.widgets.youtube_queue_item_widget.YouTubeQueueItemWidget` —
  per-row renderer embedded inside a :class:`QListWidget`.

The queue-list lifecycle — Mine/Clear/Stop, the per-item signal slots, the
terminal-bar summary, worker/processor management, curation, and the D28
selection/filter/search/reorder surface — is shared with
:class:`~anki_miner.gui.widgets.audiobook_tab.AudiobookTab` on
:class:`~anki_miner.gui.widgets._queue_mining_tab_base._ListQueueMiningTabBase`
(ARC-008). This tab adds only the link box and its two-phase Mine (queue and
check, then run), the fetcher rebuild, and the per-tab adapters (worker class,
row widget, item labels, status enum, filter bucket, search text, probe retry).

Button enable/disable is recomputed on every queue/worker signal by
:meth:`_recompute_buttons` (base). There is no explicit state enum — the queue
contents, the worker handle, the box text and ``_mine_pending`` fully determine
the UI.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils import queue_state_store
from anki_miner.gui.utils.qt_helpers import reveal_settings, urls_from_event
from anki_miner.gui.utils.queue_state_store import QueueItemSnapshot, QueueSnapshot
from anki_miner.gui.utils.run_off_thread import run_off_thread
from anki_miner.gui.utils.service_factory import create_episode_processor, create_youtube_fetcher
from anki_miner.gui.widgets._queue_mining_tab_base import (
    _ListQueueMiningTabBase,
    _QueueListStrings,
    _QueueRunStrings,
)
from anki_miner.gui.widgets.base import (
    PageWidth,
    ScreenIssue,
    configure_card_layout,
    page_filler,
)
from anki_miner.gui.widgets.base.ytdlp_availability import YtdlpAvailabilityMixin, YtdlpStrings
from anki_miner.gui.widgets.current_job_strip import CurrentJobStrip
from anki_miner.gui.widgets.enhanced import ModernButton, SectionHeader
from anki_miner.gui.widgets.log_widget import LogWidget
from anki_miner.gui.widgets.progress_widget import ProgressWidget
from anki_miner.gui.widgets.queue_controls_bar import QueueControlsBar
from anki_miner.gui.widgets.youtube_playlist_flow import (
    PlaylistAddCallbacks,
    PlaylistAddController,
    split_url_lines,
)
from anki_miner.gui.widgets.youtube_queue_item_widget import YouTubeQueueItemWidget, queue_bucket
from anki_miner.gui.workers.youtube_queue_worker import YouTubeQueueWorker
from anki_miner.interfaces.presenter import PresenterProtocol
from anki_miner.models.youtube_queue import YouTubeItemStatus, YouTubeQueue, YouTubeQueueItem
from anki_miner.orchestration import EpisodeProcessor
from anki_miner.services.asr.model_availability import usable_model_installed
from anki_miner.services.youtube_fetcher import YouTubeFetcherService
from anki_miner.utils.i18n import tr_format
from anki_miner.utils.logging_ext import log_summary
from anki_miner.utils.youtube_url import classify_youtube_url

if TYPE_CHECKING:
    from anki_miner.gui.workers._queue_worker_base import SequentialQueueWorker

logger = logging.getLogger(__name__)


class YouTubeTab(YtdlpAvailabilityMixin, _ListQueueMiningTabBase):
    """Multi-URL YouTube queue mining tab.

    The tab owns a :class:`YouTubeQueue`, a :class:`PlaylistAddController`
    holding the in-flight probe/playlist workers, and (via the base) at most one
    running :class:`YouTubeQueueWorker`. Button state is derived from the queue
    contents and the worker handle via :meth:`_recompute_buttons` (base).
    """

    #: Tables and queue rows genuinely use the extra width.
    PAGE_WIDTH = PageWidth.PAGE

    _shutdown_log_name = "YouTube"
    _status_ready = YouTubeItemStatus.READY
    _status_processing = YouTubeItemStatus.PROCESSING
    _status_completed = YouTubeItemStatus.COMPLETED
    _status_error = YouTubeItemStatus.ERROR

    #: Mine was pressed and is waiting for its links to be checked (see
    #: _start_pending_run_when_checked). Holds the queue the way a run does.
    _mine_pending: bool = False

    TASK_ID = "queue.youtube"
    TASK_OWNER = CapabilityTarget("video", "youtube")

    #: Stable filename for this queue's recovery snapshot (D16-C).
    QUEUE_STATE_KEY = "queue.youtube"

    #: The banner's "Download yt-dlp" repair. gui/app.py routes it to
    #: BackgroundTaskController.start_ytdlp_update(force=True) — this screen owns
    #: no worker of its own.
    ytdlp_download_requested = pyqtSignal()

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
            unavailable=self.tr("Mining unavailable — restart Anki Miner."),
            run_starting=self.tr("%1 run starting — %2 queued."),
            mine_label=self.tr("Mine"),
            stopped=self.tr("Stopped: %1 succeeded, %2 failed."),
            task_title=self.tr("YouTube queue"),
            retrying=self.tr("Attempt %1 of %2 · retrying in %3s"),
        )
        self._queue_list_strings = _QueueListStrings(
            cancelling=self.tr("Cancelling…"),
            stop_all=self.tr("Cancel"),
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
        # yt-dlp copy, built here so each literal stays in this tab's tr-context.
        self._ytdlp_strings = YtdlpStrings(
            missing=self.tr("yt-dlp is not installed, so YouTube mining cannot run."),
            download_action=self.tr("Download yt-dlp"),
            downloading=self.tr("Downloading yt-dlp…"),
        )
        # Probed on first show, not here: resolving re-hashes the managed binary
        # and this tab is constructed long before anyone looks at it.
        self._ytdlp_probe_needed = True
        self._ytdlp_probe_generation = 0

        self._setup_ui()

        # Add-flow controller: probe workers, playlist resolve/expand, choice
        # dialog (Issue #70). Constructed after _setup_ui so the widget-bound
        # callbacks (log_widget) exist; the tab stays the Qt parent of every
        # spawned worker thread.
        self._add_flow = PlaylistAddController(
            fetcher=fetcher,
            config=config,
            callbacks=PlaylistAddCallbacks(
                enqueue=self._queue.add,
                queued_items=self._queue.all_items,
                render_new_item=self._render_new_item,
                refresh_row=self._refresh_row,
                recompute_buttons=self._on_add_flow_changed,
                run_active=self._queue_locked,
                log_info=self.log_widget.append_info,
                log_warning=self.log_widget.append_warning,
                log_error=self.log_widget.append_error,
            ),
            parent=self,
        )

        # Seeding the caption controls runs _on_subtitle_source_changed, which
        # reaches into _add_flow — so it cannot live in _setup_ui, which runs
        # before the controller exists.
        self._seed_caption_controls()

        # Drops are answered here rather than swallowed (D50); the add flow has
        # to exist first, because a valid drop goes straight into it.
        self._setup_drag_drop()
        self._recompute_buttons()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """Build the tab layout.

        A QScrollArea wraps a Queue card (link box + list + action buttons),
        a Progress card, and a LogWidget.
        """
        scroll_area = QScrollArea()

        container = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        # --- Queue card: URL row + list + action buttons
        queue_card = QFrame()
        queue_card.setObjectName("card")
        queue_layout = QVBoxLayout()
        configure_card_layout(queue_layout)

        queue_layout.addWidget(SectionHeader(self.tr("YouTube queue")))

        # One link per line, like Utilities -> Download. Mine reads it; there is
        # no Add step. Fixed vertically, policy included: the queue list below is
        # this page's one vertical absorber, and a text edit's default Expanding
        # policy keeps the card expansive even at a fixed height.
        self.url_edit = QPlainTextEdit()
        self.url_edit.setPlaceholderText(self.tr("One YouTube link or playlist per line"))
        # Tab must move focus, not insert a literal tab (keyboard-only flow).
        self.url_edit.setTabChangesFocus(True)
        self.url_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.url_edit.setFixedHeight(self.url_edit.fontMetrics().lineSpacing() * 6 + 16)
        # The tab handles every drop on this screen (D50), including the ones
        # that land on the box itself, so a dropped link always arrives as a
        # whole line rather than wherever the text cursor happened to be.
        self.url_edit.setAcceptDrops(False)
        self.url_edit.textChanged.connect(self._refresh_mine_button)
        queue_layout.addWidget(self.url_edit)

        # Filters, search, counter and the selection actions (D28).
        self.queue_controls = QueueControlsBar()
        queue_layout.addWidget(self.queue_controls)

        # The one line describing the item actually being mined (D31).
        self.current_job_strip = CurrentJobStrip()
        queue_layout.addWidget(self.current_job_strip)

        # Queue list
        self.list_widget = QListWidget()
        self.list_widget.setObjectName("yt-queue-list")
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
        self.empty_label = QLabel(self.tr("Paste YouTube links above, one per line, then click Mine."))
        self.empty_label.setObjectName("helper-text")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        queue_layout.addWidget(self.empty_label)

        # Issue #65: opt-in per-video word curation popup (default off).
        self.review_words_checkbox = QCheckBox(self.tr("Review words before mining"))
        self._bind_review_words_checkbox()
        self.review_words_checkbox.setToolTip(self.tr("Pick which words get cards, once per video."))
        queue_layout.addWidget(self.review_words_checkbox)

        # Subtitle source, persisted like any other setting (persist_run_options
        # below). Changing it re-decides every already-probed row
        # (PlaylistAddController.set_subtitle_source).
        source_row = QHBoxLayout()
        source_row.setSpacing(SPACING.xs)
        self.subtitle_source_label = QLabel(self.tr("Subtitles:"))
        source_row.addWidget(self.subtitle_source_label)
        self.subtitle_source_combo = QComboBox()
        self.subtitle_source_combo.addItem(self.tr("Auto"), "auto")
        self.subtitle_source_combo.addItem(self.tr("Always transcribe"), "transcribe")
        self.subtitle_source_combo.addItem(self.tr("Captions only"), "captions")
        self.subtitle_source_combo.setToolTip(
            self.tr(
                "Auto uses YouTube's captions when they exist and transcribes the video "
                "when they do not. Always transcribe ignores YouTube's captions. "
                "Captions only skips a video that has none."
            )
        )
        self.subtitle_source_combo.currentIndexChanged.connect(self._on_subtitle_source_changed)
        source_row.addWidget(self.subtitle_source_combo)
        source_row.addStretch(1)
        queue_layout.addLayout(source_row)

        # Seeded from config in _seed_caption_controls, called from __init__
        # once the add flow exists.
        self.align_captions_checkbox = QCheckBox(self.tr("Align captions to audio"))
        self.align_captions_checkbox.toggled.connect(
            lambda checked: self.persist_run_options(youtube_align_captions=checked)
        )
        self.align_captions_checkbox.setToolTip(
            self.tr(
                "Retime YouTube's captions against the video's audio before mining. "
                "Ignored when the subtitle was transcribed locally."
            )
        )
        queue_layout.addWidget(self.align_captions_checkbox)

        # Action buttons
        button_row = QHBoxLayout()
        button_row.setSpacing(SPACING.xs)

        self.mine_button = ModernButton(self.tr("Mine"), variant="primary")
        self.mine_button.setToolTip(self.tr("Check every link in the box, then mine every Ready video."))
        self.mine_button.clicked.connect(self._on_mine_clicked)

        self.clear_button = ModernButton(self.tr("Clear"), variant="ghost")
        self.clear_button.setToolTip(self.tr("Remove every item from the queue."))
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
        self._install_receipt(progress_layout, self.progress_widget, item_noun=self.tr("videos"))

        progress_card.setLayout(progress_layout)
        layout.addWidget(progress_card)

        # --- LogWidget: own header + Copy/Clear actions; install_workflow_shell moves it into the Activity drawer (D6).
        self.log_widget = LogWidget(source=self.TASK_ID or type(self).__name__)

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
        self.install_issue_banner(main_layout)

    # ------------------------------------------------------------------
    # Mine: queue the pasted links, check them, then run
    # ------------------------------------------------------------------

    def _on_mine_clicked(self) -> None:
        """Queue every line in the box, then mine once every video is checked."""
        if self._queue_locked():
            return
        self.clear_screen_issue()
        if self._ytdlp_known_missing():
            # Every probe would fail the same way; refuse before queueing any.
            self._render_ytdlp_issue()
            return
        urls, rejected = split_url_lines(self.url_edit.toPlainText())
        if rejected:
            # All or nothing, like Download: half a list queued is harder to fix.
            self.show_screen_issue(
                ScreenIssue(summary=self.tr("Some lines are not valid URLs."), details="\n".join(rejected))
            )
            return
        if urls:
            self.url_edit.clear()
            self._add_flow.add_urls(urls)
        if not self._add_flow.is_busy and not self._has_rows_to_mine():
            # Everything pasted was already queued and finished: nothing to wait for.
            return
        self._mine_pending = True
        self.progress_widget.reset()
        self.progress_widget.set_status(self.tr("Checking videos…"))
        self._recompute_buttons()
        self._start_pending_run_when_checked()

    def _has_rows_to_mine(self) -> bool:
        """Whether a row is READY, or still being checked and may become so."""
        return any(i.status in (YouTubeItemStatus.READY, YouTubeItemStatus.PROBING) for i in self._queue.all_items())

    def _on_add_flow_changed(self) -> None:
        """Add-flow callback: re-derive the buttons, then start a waiting Mine if it can."""
        self._recompute_buttons()
        self._start_pending_run_when_checked()

    def _start_pending_run_when_checked(self) -> None:
        """Start the run a Mine is waiting on, once no link is still being checked.

        The worker mines a snapshot frozen at launch, so starting while a row is
        still PROBING would leave that row out of the run it was pasted for.
        """
        if not self._mine_pending or self.worker_thread is not None:
            return
        if self._add_flow.is_busy or any(i.status is YouTubeItemStatus.PROBING for i in self._queue.all_items()):
            return
        self._mine_pending = False
        self.progress_widget.reset()
        self._recompute_buttons()
        if not any(i.status is YouTubeItemStatus.READY for i in self._queue.all_items()):
            self.show_screen_issue(ScreenIssue(summary=self.tr("None of the videos can be mined. Each row says why.")))
            return
        self._start_run()

    def _on_stop_all_clicked(self) -> None:
        """Cancel a Mine still checking its links, or the run itself.

        Rows already queued stay; their probes finish on their own.
        """
        if self._mine_pending and self.worker_thread is None:
            self._log_run_control("stop")
            self._mine_pending = False
            self.progress_widget.set_status(self._queue_list_strings.cancelled)
            self._recompute_buttons()
            return
        super()._on_stop_all_clicked()

    def _queue_locked(self) -> bool:
        """A Mine waiting on its links holds the queue as a run does (D29-A)."""
        return super()._queue_locked() or self._mine_pending

    def _refresh_mine_button(self) -> None:
        """Offer Mine when there are links to check or rows to mine."""
        has_links = bool(self.url_edit.toPlainText().strip())
        self.mine_button.setEnabled(not self._queue_locked() and (has_links or self._has_rows_to_mine()))

    # ------------------------------------------------------------------
    # Durable queue contents (D16-C)
    # ------------------------------------------------------------------

    def queue_snapshot(self) -> QueueSnapshot:
        """Describe the queue as URLs and outcomes — never as probe results.

        ``video_info`` names formats, a resolved subtitle mode and a fetched
        workspace. All three are derived from a probe run against a service that
        may answer differently tomorrow, and the workspace may already have been
        cleaned up, so none of it is written. The URL is the durable fact; the
        row is re-probed on restore.
        """
        return QueueSnapshot(
            key=self.QUEUE_STATE_KEY,
            items=tuple(
                QueueItemSnapshot(
                    item_id=item.item_id,
                    source=queue_state_store.url_source(item.url, title=self._restorable_title(item)),
                    title=self._restorable_title(item),
                    status=queue_state_store.status_from_run_state(item.status.value),
                    error=item.error_message or "",
                    result_count=item.cards_created,
                )
                for item in self._queue.all_items()
            ),
        )

    @staticmethod
    def _restorable_title(item: YouTubeQueueItem) -> str:
        """The row's label, preferring the probed title over the placeholder."""
        if item.video_info is not None and item.video_info.title:
            return str(item.video_info.title)
        return item.display_title or ""

    def restore_queue_snapshot(self, snapshot: QueueSnapshot) -> int:
        """Rebuild the queue from ``snapshot`` in order; return the row count.

        Every restored row is re-probed, because that is the only way to learn
        again what a probe knew. Rows that had already finished are left alone,
        and a row that was mid-run comes back saying it was interrupted rather
        than quietly re-entering the run.
        """
        if self.worker_thread is not None or self._queue.all_items():
            return 0
        restored = 0
        reprobe: list[YouTubeQueueItem] = []
        for row in snapshot.items:
            url = str(row.source.get("url") or "")
            if not url:
                continue
            item = self._queue.add(url)
            item.item_id = row.item_id
            item.cards_created = row.result_count
            item.display_title = row.title or None
            if row.is_interrupted:
                item.status = YouTubeItemStatus.ERROR
                item.error_message = self.tr("Interrupted when Anki Miner closed")
            elif row.status == queue_state_store.STATUS_COMPLETED:
                item.status = YouTubeItemStatus.COMPLETED
            elif row.status == queue_state_store.STATUS_ERROR:
                item.status = YouTubeItemStatus.ERROR
                item.error_message = row.error
            else:
                reprobe.append(item)
            self._render_new_item(item)
            restored += 1
        for item in reprobe:
            self._add_flow.retry_probe(item)
        self._recompute_buttons()
        return restored

    # ------------------------------------------------------------------
    # Drag and drop (D50): dropped links go into the box
    # ------------------------------------------------------------------

    @staticmethod
    def _dropped_youtube_urls(event: QDragEnterEvent | QDropEvent) -> list[str]:
        """Every YouTube link a drag carries, once each, in drag order.

        A dragged link arrives as a URL and often as text as well; both are
        read, text line by line, and classified by the YouTube URL parser, so
        a link that drops is a link Mine can queue.
        """
        mime = event.mimeData()
        if mime is None:
            return []
        candidates = [url.toString() for url in urls_from_event(event)]
        if mime.hasText():
            candidates.extend(mime.text().splitlines())
        found: list[str] = []
        for candidate in candidates:
            text = candidate.strip()
            if text and text not in found and classify_youtube_url(text).kind != "unknown":
                found.append(text)
        return found

    @staticmethod
    def _carries_a_payload(event: QDragEnterEvent | QDropEvent) -> bool:
        """Whether the drag holds anything this screen could have an answer to.

        A queue reorder carries neither a URL nor text, and it belongs to the
        list widget. Reacting to it would flash the URL box red every time a row
        was dragged past the edge of the list.
        """
        mime = event.mimeData()
        return mime is not None and (mime.hasUrls() or mime.hasText())

    def _light_url_field(self, state: str) -> None:
        """Mark the URL box as the destination while a drag is over the tab."""
        self.url_edit.setProperty("dropState", state)
        if style := self.url_edit.style():
            style.unpolish(self.url_edit)
            style.polish(self.url_edit)

    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:  # noqa: N802 - Qt override
        """Light the URL box for a YouTube link; take anything else to refuse it."""
        if event is None or not self._carries_a_payload(event):
            return
        self._light_url_field("valid" if self._dropped_youtube_urls(event) else "invalid")
        event.acceptProposedAction()

    def dragLeaveEvent(self, event: QDragLeaveEvent | None) -> None:  # noqa: N802 - Qt override
        """Unlight the URL box when the drag moves off the tab."""
        self._light_url_field("")
        if event is not None:
            event.accept()

    def dropEvent(self, event: QDropEvent | None) -> None:  # noqa: N802 - Qt override
        """Queue a dropped YouTube link, or say why the payload was not one."""
        if event is None:
            return
        self._light_url_field("")
        urls = self._dropped_youtube_urls(event)
        if not urls:
            # A file is the common wrong payload here, and it has a right home.
            log_summary(
                logger,
                "YouTube drop rejected",
                level=logging.WARNING,
                reason="invalid_payload",
            )
            self.log_widget.append_warning(
                self.tr("Drop a YouTube link here. Mine local files from the Video or Audiobooks tab.")
            )
            event.ignore()
            return
        # Into the box, not the queue: Mine is the one step that queues links.
        self.url_edit.appendPlainText("\n".join(urls))
        event.acceptProposedAction()

    # ------------------------------------------------------------------
    # yt-dlp availability (YtdlpAvailabilityMixin hooks)
    # ------------------------------------------------------------------

    def _refresh_ytdlp_state(self) -> None:
        """Probe yt-dlp availability off the GUI thread (mixin hook).

        Generation-guarded like ``_ToolTabBase._run_availability_scan``: a probe
        dispatched before a config change must not overwrite a newer answer. The
        worker is not retained — ``run_off_thread`` owns it and joins it at
        shutdown, and a retained handle would only dangle after it finished.
        """
        config = self.config
        # Answered here, not only in showEvent: a probe dispatched from
        # update_config or from an update result must not leave the next show
        # re-hashing the binary a second time.
        self._ytdlp_probe_needed = False
        self._ytdlp_probe_generation += 1
        generation = self._ytdlp_probe_generation

        def _on_done(result: object) -> None:
            if generation == self._ytdlp_probe_generation:
                with contextlib.suppress(RuntimeError):
                    self._apply_probe_result(result)

        def _on_error(message: str) -> None:
            logger.warning("yt-dlp availability probe failed: %s", message)
            if generation == self._ytdlp_probe_generation:
                with contextlib.suppress(RuntimeError):
                    self._apply_probe_result(False)

        run_off_thread(self, lambda: self._compute_ytdlp_available(config), _on_done, _on_error)

    def _emit_ytdlp_download_requested(self) -> None:
        """Mixin hook: ask the window to run the updater."""
        self.ytdlp_download_requested.emit()

    def _queue_ytdlp_probe(self) -> None:
        """Probe now if this screen is on show, otherwise at its next show."""
        if self.isVisible():
            self._refresh_ytdlp_state()
        else:
            self._ytdlp_probe_needed = True

    def showEvent(self, a0) -> None:  # noqa: N802 - Qt override
        """Probe yt-dlp the first time this screen is actually looked at."""
        super().showEvent(a0)
        if self._ytdlp_probe_needed:
            self._ytdlp_probe_needed = False
            self._refresh_ytdlp_state()

    # ------------------------------------------------------------------
    # Per-tab adapters for the shared list-queue lifecycle
    # ------------------------------------------------------------------

    def _make_worker(
        self,
        items: list[Any],
        curation_callback: Callable[[list], list | None] | None,
        processor_factory: Callable[[], EpisodeProcessor] | None,
    ) -> SequentialQueueWorker[Any]:
        """Construct the YouTube queue worker (name resolves here for tests).

        The align choice is read here, on the GUI thread, and handed over as a
        plain bool — a worker thread must never touch a QWidget.
        """
        return YouTubeQueueWorker(
            processor=self._processor,
            config=self.config,
            items=items,
            curation_callback=curation_callback,
            processor_factory=processor_factory,
            align_captions=self.align_captions_checkbox.isChecked(),
        )

    def _start_run(self, items: list[Any] | None = None) -> None:
        """Refuse a transcription run with no model, then launch as usual.

        Overrides the base at its single choke point, so Mine, Retry selected
        and Reset-and-run all pass through here. The alternative is a full
        download per row followed by a raw ctranslate2 exception.
        """
        self.clear_screen_issue()
        if self._ytdlp_known_missing():
            # No fetcher, no run: refuse before the queue starts rather than
            # letting every item fail with YtdlpNotFoundError in turn.
            self._render_ytdlp_issue()
            return
        candidates = items if items is not None else self._queue.all_items()
        if any(i.resolved_sub_mode == "transcribe" for i in candidates) and not usable_model_installed(self.config):
            self.show_screen_issue(
                ScreenIssue(
                    summary=tr_format(
                        self.tr("This run needs local transcription, but the model %1 is not installed."),
                        self.config.asr_model,
                    ),
                    action_id="settings.subtitles",
                    action_text=self.tr("Open Transcription Settings"),
                ),
                action=lambda: reveal_settings(self, "subtitles"),
            )
            return
        super()._start_run(items)

    def _seed_caption_controls(self) -> None:
        """Seed the caption source and alignment from the remembered config.

        The push into the add flow is deliberately outside the guard: the flow's
        own default is ``"auto"`` and ``set_subtitle_source`` early-returns on an
        unchanged value, so a remembered ``"captions"`` would never reach it. The
        persist inside the handler compares equal and no-ops.
        """
        with self.seeding():
            index = self.subtitle_source_combo.findData(self.config.youtube_subtitle_source)
            if index >= 0:
                self.subtitle_source_combo.setCurrentIndex(index)
            self.align_captions_checkbox.setChecked(self.config.youtube_align_captions)
        self._on_subtitle_source_changed()

    def _on_subtitle_source_changed(self) -> None:
        """Adopt the picker's value and re-decide the rows already probed."""
        self._add_flow.set_subtitle_source(self.subtitle_source_combo.currentData())
        self.persist_run_options(youtube_subtitle_source=self.subtitle_source_combo.currentData())

    def _recompute_buttons(self) -> None:
        """Freeze the per-run subtitle controls while a run owns the queue.

        The worker reads ``resolved_sub_mode`` off unclaimed READY rows, and the
        sweep behind the picker rewrites exactly that field. A Mine still
        checking its links counts as owning the queue.
        """
        super()._recompute_buttons()
        idle = not self._queue_locked()
        self.subtitle_source_combo.setEnabled(idle)
        self.subtitle_source_label.setEnabled(idle)
        self.align_captions_checkbox.setEnabled(idle)
        # Mine also answers to the box text and to rows still being checked.
        self._refresh_mine_button()
        if self._mine_pending and self.worker_thread is None:
            # Checking links is not a run yet: there is no item boundary to stop at.
            self.queue_controls.pause_button.hide()
            self.queue_controls.finish_button.hide()

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

    def _filter_bucket(self, item: YouTubeQueueItem) -> str:
        """Map the item's status to a filter chip (shared with the row widget)."""
        return queue_bucket(item)

    def _search_text(self, item: YouTubeQueueItem) -> str:
        """Search the title once probed, and the URL always."""
        title = item.video_info.title if item.video_info else (item.display_title or "")
        return f"{title} {item.url}"

    def _retry_item(self, item: YouTubeQueueItem) -> bool:
        """Retry a failed row the way it failed.

        A probe failure never reached the miner, so mining it would just fail
        again for the same reason. A restored mining failure also has no probe
        metadata because snapshots store only durable URL facts. Both are
        re-probed and stay out of the retry run until that succeeds.
        """
        probe_incomplete = item.video_id is None or item.resolved_sub_mode is None or item.video_info is None
        if item.status == YouTubeItemStatus.PROBE_ERROR or probe_incomplete:
            self._add_flow.retry_probe(item)
            return False
        return super()._retry_item(item)

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
        self._queue_ytdlp_probe()
        self._seed_caption_controls()

    def shutdown(self) -> None:
        """Stop the active worker (base), then tear down the probe/playlist workers.

        The probe/playlist workers are owned by the add-flow controller, which
        gets its own shutdown call after the queue worker is joined — same
        teardown order as before the extraction.
        """
        super().shutdown()
        self._add_flow.shutdown()

    def iter_close_workers(self) -> tuple:
        """Return live add-flow workers that outlived bounded shutdown joins."""
        return self._add_flow.iter_close_workers()
