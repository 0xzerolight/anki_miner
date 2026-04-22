"""YouTube mining tab for the GUI.

Lets a user paste a YouTube URL, probe its metadata, and mine the video into
Anki cards against whatever deck / note-type is currently configured by the
global settings panel. This tab owns no deck, note-type, or tag inputs — the
existing :class:`AnkiSettingsPanel` controls those globally.

The tab runs two kinds of background work:

* :class:`~anki_miner.gui.workers.youtube_probe_worker.YouTubeProbeWorker` —
  a one-shot metadata probe spawned when the user clicks *Fetch Info*.
* :class:`~anki_miner.gui.workers.youtube_worker.YouTubeWorkerThread` —
  the full fetch + mining pipeline spawned when the user clicks *Mine*.

Button enable/disable, the status banner, and the *Accept auto-captions*
button are all driven by a single :class:`_UIState` enum so the behaviour is
deterministic and unit-testable. Transitions happen in :meth:`_transition`.
"""

from __future__ import annotations

import contextlib
from enum import Enum, auto

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.resources.styles import SPACING
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

        self._setup_ui()
        self._transition(_UIState.IDLE_NO_URL)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """Build the tab layout."""
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        # URL section -----------------------------------------------------
        layout.addWidget(SectionHeader("YouTube URL"))

        url_row = QHBoxLayout()
        url_row.setSpacing(SPACING.xs)

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://www.youtube.com/watch?v=…")
        self.url_edit.textChanged.connect(self._on_url_changed)
        url_row.addWidget(self.url_edit, 1)

        self.fetch_button = ModernButton("Fetch Info", variant="secondary")
        self.fetch_button.clicked.connect(self._on_fetch_clicked)
        url_row.addWidget(self.fetch_button)
        layout.addLayout(url_row)

        # Metadata preview -----------------------------------------------
        self.metadata_label = QLabel("")
        self.metadata_label.setWordWrap(True)
        self.metadata_label.setTextFormat(Qt.TextFormat.PlainText)
        self.metadata_label.setObjectName("youtube-metadata")
        layout.addWidget(self.metadata_label)

        # Status banner --------------------------------------------------
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("youtube-status")
        layout.addWidget(self.status_label)

        # Accept auto-captions button ------------------------------------
        self.accept_button = ModernButton("Accept auto-captions and mine", variant="secondary")
        self.accept_button.clicked.connect(self._on_accept_auto_clicked)
        self.accept_button.hide()
        layout.addWidget(self.accept_button)

        # Mine button ----------------------------------------------------
        layout.addWidget(SectionHeader("Mine"))
        mine_row = QHBoxLayout()
        mine_row.setSpacing(SPACING.xs)

        self.mine_button = ModernButton("Mine", variant="primary")
        self.mine_button.clicked.connect(self._on_mine_clicked)
        self.mine_button.setEnabled(False)
        mine_row.addWidget(self.mine_button)

        self.cancel_button = ModernButton("Cancel", variant="danger")
        self.cancel_button.clicked.connect(self._on_cancel_clicked)
        self.cancel_button.hide()
        mine_row.addWidget(self.cancel_button)
        mine_row.addStretch()
        layout.addLayout(mine_row)

        # Progress + log -------------------------------------------------
        self.progress_widget = ProgressWidget()
        layout.addWidget(self.progress_widget)

        self.log_widget = LogWidget()
        layout.addWidget(self.log_widget)

        # Divider (purely cosmetic; keeps tab content from hugging buttons)
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(divider)

        self.setLayout(layout)

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

    def _on_mine_clicked(self) -> None:
        """Kick off the fetch+mine pipeline."""
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

        worker = YouTubeWorkerThread(
            processor=self._processor,
            config=self._config,
            url=url,
            video_id=self._video_info.video_id,
            sub_mode=self._resolved_sub_mode,
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
        self.mine_button.setEnabled(mine_enabled)
        self.fetch_button.setEnabled(fetch_enabled)

        if accept_visible:
            self.accept_button.show()
        else:
            self.accept_button.hide()

        if cancel_visible:
            self.cancel_button.setText("Cancel")
            self.cancel_button.setEnabled(True)
            self.cancel_button.show()
        else:
            self.cancel_button.hide()

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
