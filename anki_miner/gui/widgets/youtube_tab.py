"""YouTube mining tab for the GUI.

Lets a user paste a YouTube URL, probe its metadata, and mine the video into
Anki cards against whatever deck / note-type is currently configured by the
global settings panel. This tab owns no deck, note-type, or tag inputs — the
existing :class:`AnkiSettingsPanel` controls those globally.

The tab runs two kinds of background work:

* :class:`~anki_miner.gui.workers.youtube_probe_worker.YouTubeProbeWorker` —
  a one-shot metadata probe spawned when the user clicks *Fetch Info*.
* :class:`~anki_miner.gui.workers.youtube_worker.YouTubeWorkerThread` —
  the full fetch + mining pipeline spawned when the user clicks
  *Preview Words* or *Process Video*.

Button enable/disable, the status banner, and the *Accept auto-captions*
button are all driven by a single :class:`_UIState` enum so the behaviour is
deterministic and unit-testable. Transitions happen in :meth:`_transition`.
"""

from __future__ import annotations

import contextlib
import threading
from enum import Enum, auto

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.service_factory import create_youtube_fetcher
from anki_miner.gui.widgets.enhanced import ModernButton, SectionHeader
from anki_miner.gui.widgets.log_widget import LogWidget
from anki_miner.gui.widgets.progress_widget import ProgressWidget
from anki_miner.gui.workers.youtube_probe_worker import YouTubeProbeWorker
from anki_miner.gui.workers.youtube_worker import YouTubeWorkerThread
from anki_miner.interfaces.presenter import PresenterProtocol
from anki_miner.models.youtube import SubMode, VideoInfo
from anki_miner.orchestration import EpisodeProcessor
from anki_miner.services.youtube_fetcher import YouTubeFetcherService


class _UIState(Enum):
    """Finite states that drive button/status rendering for the YouTube tab."""

    IDLE_NO_URL = auto()
    PROBING = auto()
    PROBE_ERROR = auto()
    LIVE = auto()
    TOO_LONG = auto()
    AGE_LOCKED = auto()
    NO_SUBS = auto()
    MANUAL_READY = auto()
    AUTO_PENDING = auto()
    AUTO_READY = auto()
    MINING = auto()
    MINED = auto()
    MINE_ERROR = auto()


def _format_duration(seconds: int) -> str:
    """Format ``seconds`` as ``Xm Ys`` or ``Hh Mm Ss``."""
    if seconds < 0:
        seconds = 0
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    return f"{minutes}m {secs}s"


def _subs_label(info: VideoInfo) -> str:
    """Short human label for the subtitle availability on a probed video."""
    if info.has_manual_ja_subs:
        return "Manual JA"
    if info.has_auto_ja_subs:
        return "Native auto JA"
    return "None"


class YouTubeTab(QWidget):
    """Tab widget for mining Japanese vocabulary from a YouTube video."""

    # Cross-thread curation bridge: emitted from the worker thread, handled on
    # the GUI thread. Mirrors the pattern in SingleEpisodeTab.
    _curation_requested = pyqtSignal(list)

    def __init__(
        self,
        config: AnkiMinerConfig,
        processor: EpisodeProcessor,
        fetcher: YouTubeFetcherService,
        presenter: PresenterProtocol | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """Initialize the tab.

        Args:
            config: Frozen application configuration.
            processor: Episode processor (shared across tabs).
            fetcher: YouTube fetcher service used for metadata probes and,
                indirectly via ``processor.process_youtube_url``, downloads.
            presenter: Optional presenter for routing log messages. Log output
                for mining runs flows through the worker's progress signal
                instead; this is kept for parity with other tabs.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._config = config
        self._processor = processor
        self._fetcher = fetcher
        self._presenter = presenter

        # Worker handles — exposed for main_window's closeEvent.
        self.worker_thread: YouTubeWorkerThread | None = None
        self._probe_worker: YouTubeProbeWorker | None = None

        # Latest probe result, and the resolved sub_mode once the user has
        # accepted whatever is available. Both stay None until a successful
        # probe settles on a READY state.
        self._video_info: VideoInfo | None = None
        self._resolved_sub_mode: SubMode | None = None

        # State machine; transitions go through _transition.
        self._state: _UIState = _UIState.IDLE_NO_URL

        # Curation bridge: the worker thread blocks on this event while the
        # GUI thread shows the curation dialog. Mirrors SingleEpisodeTab.
        self._curation_event = threading.Event()
        self._curation_result: list = []
        self._curation_requested.connect(self._on_curation_requested)

        self._setup_ui()
        self._transition(_UIState.IDLE_NO_URL)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """Build the tab layout.

        Structure mirrors SingleEpisodeTab: a QScrollArea wraps a container
        of card-style QFrame groups (Source / Actions / Progress) plus a
        standalone LogWidget.
        """
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        # --- Source card: URL + metadata + status + accept-auto-captions
        source_card = QFrame()
        source_card.setObjectName("card")
        source_layout = QVBoxLayout()
        source_layout.setSpacing(SPACING.sm)
        source_layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        source_layout.addWidget(SectionHeader("YouTube URL"))

        url_row = QHBoxLayout()
        url_row.setSpacing(SPACING.xs)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://www.youtube.com/watch?v=…")
        self.url_edit.textChanged.connect(self._on_url_changed)
        url_row.addWidget(self.url_edit, 1)

        self.fetch_button = ModernButton("Fetch Info", variant="secondary")
        self.fetch_button.clicked.connect(self._on_fetch_clicked)
        url_row.addWidget(self.fetch_button)
        source_layout.addLayout(url_row)

        self.metadata_label = QLabel("")
        self.metadata_label.setWordWrap(True)
        self.metadata_label.setTextFormat(Qt.TextFormat.PlainText)
        self.metadata_label.setObjectName("youtube-metadata")
        source_layout.addWidget(self.metadata_label)

        # Status banner: uses the "helper-text" QSS rule (small, italic,
        # muted). One label serves both the idle hint and status/error
        # strings.
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setObjectName("helper-text")
        source_layout.addWidget(self.status_label)

        self.accept_button = ModernButton("Accept auto-captions and mine", variant="secondary")
        self.accept_button.clicked.connect(self._on_accept_auto_clicked)
        self.accept_button.hide()
        source_layout.addWidget(self.accept_button)

        source_card.setLayout(source_layout)
        layout.addWidget(source_card)

        # --- Actions card: Preview + Process + Cancel
        actions_card = QFrame()
        actions_card.setObjectName("card")
        actions_layout = QVBoxLayout()
        actions_layout.setSpacing(SPACING.sm)
        actions_layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        actions_layout.addWidget(SectionHeader("Actions"))

        button_row = QHBoxLayout()
        button_row.setSpacing(SPACING.xs)

        self.preview_button = ModernButton("Preview Words", variant="secondary")
        self.preview_button.setToolTip("Show discovered words without creating Anki cards.")
        self.preview_button.clicked.connect(self._on_preview_clicked)
        self.preview_button.setEnabled(False)

        self.process_button = ModernButton("Process Video", variant="primary")
        self.process_button.setToolTip("Curate words, then create Anki cards from the video.")
        self.process_button.clicked.connect(self._on_process_clicked)
        self.process_button.setEnabled(False)

        self.cancel_button = ModernButton("Cancel", variant="danger")
        self.cancel_button.clicked.connect(self._on_cancel_clicked)
        self.cancel_button.hide()

        button_row.addWidget(self.preview_button)
        button_row.addWidget(self.process_button)
        button_row.addWidget(self.cancel_button)
        button_row.addStretch()
        actions_layout.addLayout(button_row)

        actions_card.setLayout(actions_layout)
        layout.addWidget(actions_card)

        # --- Progress card
        progress_card = QFrame()
        progress_card.setObjectName("card")
        progress_layout = QVBoxLayout()
        progress_layout.setSpacing(SPACING.sm)
        progress_layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        progress_layout.addWidget(SectionHeader("Progress"))
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
    # Event handlers
    # ------------------------------------------------------------------

    def _on_url_changed(self, _text: str) -> None:
        """Reset the state machine whenever the URL field is edited.

        Any existing probe result is invalidated — the user must press Fetch
        Info again before they can mine. This keeps the Mine button from
        firing against stale metadata.
        """
        if self._state in (_UIState.MINING,):
            # Don't yank state out from under an active run.
            return
        self._video_info = None
        self._resolved_sub_mode = None
        self.metadata_label.setText("")
        url = self.url_edit.text().strip()
        if not url:
            self._transition(_UIState.IDLE_NO_URL)
        else:
            # URL present but unprobed — still in the IDLE bucket until the
            # user hits Fetch Info.
            self._transition(_UIState.IDLE_NO_URL)

    def _on_fetch_clicked(self) -> None:
        """Kick off a metadata probe for the current URL."""
        url = self.url_edit.text().strip()
        if not url:
            self._transition(_UIState.IDLE_NO_URL)
            return
        # Reject overlapping probes.
        if self._probe_worker is not None and self._probe_worker.isRunning():
            return

        # Defensively detach signal slots from any retired worker still pending
        # GC so a late ``finished`` emission cannot clobber a fresh probe's
        # ``_probe_worker`` handle via ``_on_probe_finished``.
        if self._probe_worker is not None:
            try:
                self._probe_worker.probe_done.disconnect(self._on_probe_done)
                self._probe_worker.probe_error.disconnect(self._on_probe_error)
                self._probe_worker.finished.disconnect(self._on_probe_finished)
            except TypeError:
                # Already disconnected (e.g. _on_probe_finished ran cleanly).
                pass

        self._transition(_UIState.PROBING)
        worker = YouTubeProbeWorker(self._fetcher, url)
        worker.probe_done.connect(self._on_probe_done)
        worker.probe_error.connect(self._on_probe_error)
        worker.finished.connect(self._on_probe_finished)
        self._probe_worker = worker
        worker.start()

    def _on_probe_done(self, info: object) -> None:
        """Handle a successful probe result. ``info`` is a VideoInfo."""
        if not isinstance(info, VideoInfo):  # pragma: no cover - signal guard
            self._transition(_UIState.PROBE_ERROR, error="Invalid probe result.")
            return
        self._video_info = info
        self._render_metadata(info)
        self._transition(self._classify_video(info))

    def _on_probe_error(self, message: str) -> None:
        """Handle a probe failure (any exception from yt-dlp)."""
        self._video_info = None
        self.metadata_label.setText("")
        self._transition(_UIState.PROBE_ERROR, error=message)

    def _on_probe_finished(self) -> None:
        """Clear the probe worker handle once the QThread signals finished."""
        self._probe_worker = None

    def _on_accept_auto_clicked(self) -> None:
        """User accepted the auto-caption warning — arm the Mine button."""
        if self._state != _UIState.AUTO_PENDING:
            return
        self._transition(_UIState.AUTO_READY)

    def _on_preview_clicked(self) -> None:
        """Preview Words button: run pipeline with ``preview_mode=True``."""
        self._start_mining(preview_mode=True)

    def _on_process_clicked(self) -> None:
        """Process Video button: run pipeline with ``preview_mode=False``."""
        self._start_mining(preview_mode=False)

    def _start_mining(self, *, preview_mode: bool) -> None:
        """Kick off the fetch+mine pipeline.

        Curation callback is wired only on Process, never on Preview. The
        processor also gates curation on ``not preview_mode`` so this is
        belt-and-braces rather than load-bearing.
        """
        if self._state not in (_UIState.MANUAL_READY, _UIState.AUTO_READY, _UIState.MINED):
            return
        if self._video_info is None or self._resolved_sub_mode is None:
            return

        url = self.url_edit.text().strip()
        if not url:
            return

        self.log_widget.clear_log()
        self.progress_widget.reset()
        self._transition(_UIState.MINING)

        curation_cb = self._curation_bridge if not preview_mode else None

        worker = YouTubeWorkerThread(
            processor=self._processor,
            config=self._config,
            url=url,
            video_id=self._video_info.video_id,
            sub_mode=self._resolved_sub_mode,
            curation_callback=curation_cb,
            preview_mode=preview_mode,
        )
        worker.progress.connect(self._on_mine_progress)
        worker.result_ready.connect(self._on_mine_finished)
        worker.error.connect(self._on_mine_error)
        worker.finished.connect(self._on_worker_finished)
        self.worker_thread = worker
        worker.start()

    def _on_cancel_clicked(self) -> None:
        """Request cancellation of the active mining run."""
        if self.worker_thread is not None:
            self.worker_thread.cancel()
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText("Cancelling…")

    def _on_mine_progress(self, label: str, pct: int) -> None:
        """Route worker progress into the progress widget."""
        if pct < 0:
            self.progress_widget.set_indeterminate()
        else:
            self.progress_widget.set_determinate(100)
            self.progress_widget.set_value(pct)
        self.progress_widget.set_status(label)

    def _on_mine_finished(self, result: object) -> None:
        """Handle a successful mining result."""
        # ``ProcessingResult.cards_created`` is the canonical count; the
        # fallbacks keep the tab resilient to schema tweaks.
        cards_added = getattr(result, "cards_created", None)
        if cards_added is None:
            cards_added = getattr(result, "cards_added", 0)
        message = f"Mining complete. {cards_added} cards added."
        self._transition(_UIState.MINED, message=message)
        if self._presenter is not None:
            # Presenter forwarding is best-effort — the log widget already
            # has the mining result; we don't want a broken presenter slot
            # to bubble up as a mining failure.
            with contextlib.suppress(Exception):  # pragma: no cover
                self._presenter.show_processing_result(result)  # type: ignore[arg-type]

    def _on_mine_error(self, error_message: str) -> None:
        """Handle a worker error; re-enable Mine for retry."""
        self._transition(_UIState.MINE_ERROR, error=error_message)

    def _on_worker_finished(self) -> None:
        """Clear the worker handle whenever the QThread finishes."""
        self.worker_thread = None

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    def _classify_video(self, info: VideoInfo) -> _UIState:
        """Pick the next state based on a fresh ``VideoInfo``.

        Ordering matters: live-stream and duration gates take precedence
        over subtitle availability because the latter is irrelevant if the
        video can't be fetched.
        """
        if info.is_live:
            return _UIState.LIVE
        if info.duration_s > self._config.youtube_max_duration_s:
            return _UIState.TOO_LONG
        if info.is_age_restricted and not self._config.youtube_cookies_from_browser:
            return _UIState.AGE_LOCKED
        if info.has_manual_ja_subs:
            return _UIState.MANUAL_READY
        if info.has_auto_ja_subs:
            return _UIState.AUTO_PENDING
        return _UIState.NO_SUBS

    def _transition(
        self,
        new_state: _UIState,
        *,
        error: str | None = None,
        message: str | None = None,
    ) -> None:
        """Enter ``new_state`` and refresh all observable UI properties.

        Args:
            new_state: State to enter.
            error: Error text for PROBE_ERROR / MINE_ERROR states.
            message: Override for the default status text (used by MINED to
                include the card count).
        """
        self._state = new_state

        # Defaults; per-state code overrides these below.
        mine_enabled = False
        cancel_visible = False
        accept_visible = False
        fetch_enabled = True
        status_text = ""

        if new_state == _UIState.IDLE_NO_URL:
            status_text = "Enter a YouTube URL and click Fetch Info."

        elif new_state == _UIState.PROBING:
            fetch_enabled = False
            status_text = "Fetching metadata…"

        elif new_state == _UIState.PROBE_ERROR:
            status_text = error or "Probe failed."

        elif new_state == _UIState.LIVE:
            status_text = "Live streams are not supported."

        elif new_state == _UIState.TOO_LONG:
            minutes_limit = max(1, self._config.youtube_max_duration_s // 60)
            status_text = f"Video exceeds max duration ({minutes_limit} min)."

        elif new_state == _UIState.AGE_LOCKED:
            status_text = "Age-restricted video. Set Cookies → Browser in Settings and retry."

        elif new_state == _UIState.NO_SUBS:
            status_text = "No Japanese subtitles available for this video."

        elif new_state == _UIState.MANUAL_READY:
            self._resolved_sub_mode = "manual_only"
            mine_enabled = True
            status_text = "Manual Japanese subtitles detected — ready to mine."

        elif new_state == _UIState.AUTO_PENDING:
            self._resolved_sub_mode = None
            accept_visible = True
            status_text = (
                "Japanese auto-captions detected (no manual subs). " "Quality may be lower."
            )

        elif new_state == _UIState.AUTO_READY:
            self._resolved_sub_mode = "auto_only"
            mine_enabled = True
            accept_visible = False
            status_text = "Auto-captions accepted — ready to mine."

        elif new_state == _UIState.MINING:
            mine_enabled = False
            fetch_enabled = False
            cancel_visible = True
            status_text = message or "Mining in progress…"

        elif new_state == _UIState.MINED:
            mine_enabled = True
            status_text = message or "Mining complete."

        elif new_state == _UIState.MINE_ERROR:
            mine_enabled = True
            status_text = error or "Mining failed."

        self.status_label.setText(status_text)
        self.preview_button.setEnabled(mine_enabled)
        self.process_button.setEnabled(mine_enabled)
        self.fetch_button.setEnabled(fetch_enabled)

        if accept_visible:
            self.accept_button.show()
        else:
            self.accept_button.hide()

        if cancel_visible:
            self.preview_button.hide()
            self.process_button.hide()
            self.cancel_button.setText("Cancel")
            self.cancel_button.setEnabled(True)
            self.cancel_button.show()
        else:
            self.cancel_button.hide()
            self.preview_button.show()
            self.process_button.show()

    # ------------------------------------------------------------------
    # Curation bridge
    # ------------------------------------------------------------------

    def _curation_bridge(self, words: list) -> list:
        """Thread-safe curation bridge: blocks the worker until the dialog closes.

        Called from the worker thread. Emits a signal to the GUI thread so the
        dialog runs on the correct thread, then blocks on ``_curation_event``
        until the GUI slot completes. Returns the user's selected words.
        """
        self._curation_event.clear()
        self._curation_result = []
        self._curation_requested.emit(words)
        self._curation_event.wait()
        return self._curation_result

    def _on_curation_requested(self, words: list) -> None:
        """Slot on the GUI thread that runs the curation dialog."""
        from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog

        dialog = WordCurationDialog(words, self)
        if dialog.exec() == WordCurationDialog.DialogCode.Accepted:
            self._curation_result = dialog.get_selected_words()
        else:
            self._curation_result = []
        self._curation_event.set()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _render_metadata(self, info: VideoInfo) -> None:
        """Populate the metadata preview label with a probed video's info."""
        lines = [
            f"Title: {info.title}",
            f"Uploader: {info.uploader or 'Unknown'}",
            f"Duration: {_format_duration(info.duration_s)}",
            f"Subtitles: {_subs_label(info)}",
        ]
        self.metadata_label.setText("\n".join(lines))

    def update_config(self, config: AnkiMinerConfig) -> None:
        """Adopt a new frozen config and refresh any config-dependent UI.

        Called when the Settings tab emits ``config_changed``. Updates the
        cached fetcher (which snapshots config on construction) so that
        subsequent probes and mining runs honour the latest values, and
        re-classifies the already-probed video if the new duration limit
        changes the verdict.

        Args:
            config: New frozen configuration.
        """
        self._config = config

        # Fetcher snapshots config on construction (see YouTubeFetcherService
        # __init__) and is used for both probe_metadata and, through the
        # worker thread, the full fetch pipeline. Recreate via the factory
        # to keep construction routed through a single code path.
        self._fetcher = create_youtube_fetcher(config)

        # If we're not in the middle of a run, re-classify any already-probed
        # video against the new config so the TOO_LONG gate reflects the
        # freshly-saved duration limit and the status label is refreshed.
        if self._state == _UIState.MINING:
            return
        if self._video_info is not None:
            self._transition(self._classify_video(self._video_info))

    def shutdown(self) -> None:
        """Stop any running worker threads.

        Called by :class:`MainWindow` during closeEvent so that background
        threads don't outlive the application.
        """
        if self._probe_worker is not None:
            self._probe_worker.quit()
            self._probe_worker.wait()
            self._probe_worker = None
        if self.worker_thread is not None:
            self.worker_thread.cancel()
            self.worker_thread.quit()
            self.worker_thread.wait()
            self.worker_thread = None
