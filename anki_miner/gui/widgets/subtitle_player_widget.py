"""Reusable video player widget with subtitle overlay."""

import logging
import time
from pathlib import Path

from PyQt6.QtCore import QCoreApplication, QEvent, QLocale, Qt, QTimer, QUrl
from PyQt6.QtMultimedia import QAudioOutput, QMediaMetaData, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget

from anki_miner.gui.resources.styles.theme import Theme
from anki_miner.gui.utils.run_off_thread import join_tracked_workers, run_off_thread
from anki_miner.utils import find_japanese_audio_stream, get_primary_video_codec
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)

# Base subtitle-overlay font size (px) at scale 1.0.
_BASE_OVERLAY_FONT_PX = 18

# AV1 decode-watchdog timeout (ms). The first hardware-AV1 decode in the process
# can stall on GPU pipeline cold-init (D3D11VA device + decoder session), which
# can exceed several seconds even when steady-state decode is fast, so the deadline
# must clear cold-init rather than just decode time. Generous so a real (but slow)
# cold decode isn't cut off and mistaken for "can't decode" (Issue #82).
_AV1_WATCHDOG_MS = 10000

# Fallback position (ms) the AV1 decode nudge seeks to when no subtitle timestamp is
# available. A few hundred ms (not ~0) so the seek lands on a different keyframe than
# frame 0 and the backend issues a genuine demux+decode rather than a no-op; a 1 ms
# seek coalesces to frame 0 and decodes nothing (Issue #82). When subtitle entries
# exist the nudge seeks to the first one instead.
_AV1_NUDGE_FALLBACK_MS = 500

# Resolved at import time so they remain correct even when QMediaPlayer is
# patched in unit tests (which replaces the module-level name with a MagicMock).
_LOADED_MEDIA = QMediaPlayer.MediaStatus.LoadedMedia
_BUFFERED_MEDIA = QMediaPlayer.MediaStatus.BufferedMedia


class SubtitlePlayerWidget(QWidget):
    """Reusable video player with subtitle overlay, extracted from SubtitleViewer.

    Owns QVideoWidget, overlay QLabel, position QSlider, time label, play/pause button,
    QMediaPlayer, and QAudioOutput. No player is created until set_source is called.
    """

    def __init__(self, parent=None):
        """Initialize the player widget (no media until set_source is called).

        Args:
            parent: Optional parent widget.
        """
        super().__init__(parent)

        # Will be populated by set_source
        self.subtitle_entries: list[tuple[float, float, str]] = []
        self._offset: float = 0.0
        self._jp_audio_index: int | None = None
        self._audio_track_override: int | None = None

        # Player is None until set_source is called
        self.player: QMediaPlayer | None = None
        self.audio_output: QAudioOutput | None = None

        # Re-entrancy guard for the async ffprobe in set_source. The counter only
        # ever increases: every set_source and every teardown bumps it, so any
        # generation captured by an outstanding probe no longer matches once a
        # newer set_source or a teardown has run. The probe's GUI-thread callback
        # no-ops whenever its captured generation differs from the current one,
        # which is exactly how a superseded or post-teardown probe is dropped.
        self._source_generation: int = 0
        self._probe_worker: object | None = None
        # set_source is now async (the player is built only after the off-thread
        # ffprobe returns), so a play() that arrives before the probe lands is
        # remembered here and honoured once the player exists. pause()/stop()
        # clear it; a new set_source resets it.
        self._pending_play: bool = False
        # seek_seconds()/pause() are async-symmetric with play(): a seek or pause
        # issued while the probe is still in flight (player is None) is remembered
        # here and applied in _configure_player once the player exists — otherwise
        # WordCurationDialog's seek-then-pause preview (issued during construction,
        # before the probe lands) silently no-ops and the player rests at frame 0.
        self._pending_seek_ms: int | None = None
        self._pending_pause: bool = False

        # AV1 watchdog state — populated by set_source
        self._is_av1: bool = False
        self._got_video_frame: bool = False
        # Set when the media loads while the widget is still hidden; the decode
        # nudge + watchdog are then armed from showEvent instead. The nudge seeks
        # the video sink, which only presents a frame once it is on screen, so
        # nudging/arming while hidden would seek into the void and fire a false
        # fallback (Issue #82).
        self._av1_watchdog_pending: bool = False

        # Single-shot watchdog: fires _AV1_WATCHDOG_MS after LoadedMedia/BufferedMedia
        # if no frame decoded.
        self._av1_watchdog = QTimer(self)
        self._av1_watchdog.setSingleShot(True)
        self._av1_watchdog.timeout.connect(self._on_av1_watchdog_timeout)

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the player UI layout."""
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Video widget
        self.video_widget = QVideoWidget()
        layout.addWidget(self.video_widget, 1)

        # AV1 fallback UI — hidden by default; shown when the watchdog fires on an
        # undecodable AV1 source (GPU can't hardware-decode AV1 on this machine).
        # The player is left running, so audio + subtitle overlay keep playing for
        # sync checking; only the video frame is unavailable.
        self._av1_notice_label = QLabel(
            self.tr(
                "This video uses AV1, which your system can't decode for in-app preview. "
                "Audio and subtitles still play."
            )
        )
        self._av1_notice_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._av1_notice_label.setWordWrap(True)
        self._av1_notice_label.setVisible(False)
        layout.addWidget(self._av1_notice_label)

        # Connect the video-sink signal once; the sink belongs to the QVideoWidget
        # and persists across player instances, so we wire it here rather than per-source.
        # videoSink() is typed Optional but a constructed QVideoWidget always has one.
        video_sink = self.video_widget.videoSink()
        if video_sink is not None:
            video_sink.videoFrameChanged.connect(self._on_video_frame_changed)

        # Subtitle overlay label
        self.subtitle_label = QLabel()
        self.subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.subtitle_label.setWordWrap(True)
        # Overlay font is set at construction, reflecting the scale at that time.
        # The player widget is created per session/playback, so this is fine —
        # no live re-scaling plumbing (YAGNI).
        overlay_font_px = max(1, round(_BASE_OVERLAY_FONT_PX * Theme.get_font_scale()))
        self.subtitle_label.setStyleSheet(
            "QLabel { background-color: rgba(0,0,0,180); color: white; "
            f"font-size: {overlay_font_px}px; padding: 6px 12px; border-radius: 4px; }}"
        )
        self.subtitle_label.setVisible(False)
        layout.addWidget(self.subtitle_label)

        # Position slider and time display
        position_layout = QHBoxLayout()
        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.setRange(0, 0)
        self.position_slider.sliderMoved.connect(self._on_slider_moved)

        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setMinimumWidth(100)

        position_layout.addWidget(self.position_slider, 1)
        position_layout.addWidget(self.time_label)
        layout.addLayout(position_layout)

        # Play/pause button row
        controls_layout = QHBoxLayout()
        self.play_button = QPushButton(self.tr("Play"))
        self.play_button.setFixedWidth(80)
        self.play_button.clicked.connect(self.toggle_play_pause)
        controls_layout.addWidget(self.play_button)
        controls_layout.addStretch()
        layout.addLayout(controls_layout)

        self.setLayout(layout)

    def set_source(
        self,
        video_path: Path,
        subtitle_entries: list[tuple[float, float, str]],
        offset: float = 0.0,
        *,
        audio_track_override: int | None = None,
        ffprobe_cmd: str = "ffprobe",
    ) -> None:
        """Load a video source and configure subtitle playback.

        If called again on an already-loaded source, the previous player is stopped first.

        Args:
            video_path: Path to the video file.
            subtitle_entries: List of (start_seconds, end_seconds, text) tuples.
            offset: Initial subtitle timing offset in seconds.
            audio_track_override: Optional 0-indexed audio track to force instead of
                auto-detecting Japanese. None preserves auto-detect.
            ffprobe_cmd: ffprobe executable path/literal used for audio-track auto-detection.
                Defaults to the bare ``"ffprobe"`` literal (PATH lookup).

        The two ffprobe subprocesses (codec detection + Japanese-audio detection)
        run on a worker thread so the GUI stays responsive while probing; the
        QMediaPlayer is built in a GUI-thread callback once the probe returns
        (a fraction of a second later). The public signature is unchanged.
        """
        # Stop and fully tear down any existing player before re-initialising.
        self._teardown_player()

        # Reset per-source watchdog state and restore normal video widget visibility.
        self._got_video_frame = False
        self._av1_watchdog_pending = False
        self._av1_watchdog.stop()
        self._av1_notice_label.setVisible(False)
        self.video_widget.setVisible(True)

        self.subtitle_entries = subtitle_entries
        self._offset = offset
        self._audio_track_override = audio_track_override

        # A new source cancels any auto-play / queued seek / pause against the previous one.
        self._pending_play = False
        self._pending_seek_ms = None
        self._pending_pause = False

        # Bump the generation and capture it for the closure: a later set_source
        # supersedes this probe, so its callback must no-op when it finally lands.
        self._source_generation += 1
        generation = self._source_generation

        def _probe() -> tuple[bool, int | None]:
            """Run the blocking ffprobe calls on the worker thread."""
            is_av1 = get_primary_video_codec(video_path, ffprobe_cmd=ffprobe_cmd) == "av1"
            if audio_track_override is None:
                jp_stream = find_japanese_audio_stream(video_path, ffprobe_cmd=ffprobe_cmd)
                jp_audio_index = jp_stream.audio_index if jp_stream is not None else None
            else:
                jp_audio_index = audio_track_override
            return is_av1, jp_audio_index

        def _configure(result: object) -> None:
            """Build the player on the GUI thread once the probe returns."""
            self._configure_player(generation, video_path, result)  # type: ignore[arg-type]

        def _on_probe_error(msg: str) -> None:
            """Surface a probe failure instead of leaving a silently blank player."""
            if generation != self._source_generation:
                return
            logger.warning("Subtitle player ffprobe failed: %s", msg)
            self.subtitle_label.setText(tr_format(self.tr("Could not load video: %1"), msg))
            self.subtitle_label.setVisible(True)

        self._probe_worker = run_off_thread(
            self,
            _probe,
            _configure,
            _on_probe_error,
            error_prefix="ffprobe failed: ",
        )

    def _configure_player(
        self,
        generation: int,
        video_path: Path,
        result: tuple[bool, int | None],
    ) -> None:
        """Build the QMediaPlayer from probe results (GUI thread).

        A no-op if a newer ``set_source`` superseded this one (generation guard)
        or the widget is being torn down, so a probe finishing after the video
        widget's C++ object is gone can't touch it.
        """
        if generation != self._source_generation:
            return

        is_av1, jp_audio_index = result
        self._is_av1 = is_av1
        self._jp_audio_index = jp_audio_index

        self.audio_output = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)

        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self._on_duration_changed)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.player.errorOccurred.connect(self._on_media_error)
        self.player.tracksChanged.connect(self._on_tracks_changed)
        self.player.mediaStatusChanged.connect(self._on_media_status_changed)

        self.player.setSource(QUrl.fromLocalFile(str(video_path)))

        # Honour a seek/pause/play that arrived while the probe was still in
        # flight. Seek first so playback (or the paused first-frame preview)
        # lands at the requested position rather than frame 0.
        if self._pending_seek_ms is not None:
            self.player.setPosition(self._pending_seek_ms)
            self._pending_seek_ms = None
        if self._pending_pause:
            self._pending_pause = False
            self.player.pause()
        if self._pending_play:
            self._pending_play = False
            self.player.play()

    def _teardown_player(self) -> None:
        """Stop playback and detach both outputs from the current player.

        Factored out of ``set_source`` so it can be called from both re-source
        (reuse path) and ``closeEvent`` (widget discard path).  With both
        ``QAudioOutput`` and ``QMediaPlayer`` parented to ``self``, Qt will free
        the C++ objects when the widget is destroyed — but detaching the outputs
        first is still best practice to avoid a use-after-free window during
        the Qt object-tree teardown.  ``set_source`` builds a fresh player AND a
        fresh ``QAudioOutput`` on every re-source, so the old output is scheduled
        for deletion here too — otherwise one ``QAudioOutput`` accumulates under
        the widget per re-source until the widget itself is destroyed (F9).

        Windows GUI-freeze fix (Test Timing → Apply Offset / Cancel "Not
        Responding"): we now detach the VIDEO sink with ``setVideoOutput(None)``
        — right after ``stop()`` — before the player is destroyed. The Qt6 FFmpeg
        backend (the Windows default) hangs the GUI thread destroying a
        ``QMediaPlayer`` whose D3D video sink is still attached; detaching it
        first clears that. It is sequenced AFTER ``stop()`` (not before) so the
        sink is never detached mid-``PlayingState`` — the Test Timing flow leaves
        the player playing at teardown; detaching from a stopped player is safe in
        every state (Playing/Paused/Stopped), which is all the curation/AV1 paths
        need too. Qt documents no blocking/ordering contract here, so WHICH call
        blocks on Windows is a hypothesis: the per-step DEBUG timing below, plus a
        forced (drained) destruction, exist to localize it in a single Windows run.

        Also cancels/short-joins any in-flight ffprobe worker first — its callback
        would otherwise build a player into a half-torn-down widget. A probe stuck
        past the join is detached from the widget (see ``_join_probe_worker``) so
        a QThread is never destroyed while running. The join must precede the
        ``self.player is None`` early-out, since that None is exactly the
        in-flight-probe state (the player is not built until the callback).
        """
        self._join_probe_worker()

        if self.player is None:
            return
        # Bind a non-Optional local so the deferred ``_timed`` callables and the
        # drain don't re-widen ``self.player`` back to Optional; same object, so
        # mock assertions in tests are unaffected.
        player = self.player

        # Diagnostics are DEBUG-gated so default runtime AND default test runs are
        # byte-for-byte unaffected (the forced DeferredDelete drain below would
        # raise TypeError against a mocked player otherwise). Only setVideoOutput
        # (None) is an always-on behavior change — the actual fix.
        debug = logger.isEnabledFor(logging.DEBUG)

        def _timed(label: str, call):
            """Run ``call`` and, under DEBUG, log how long it took.

            No ``try``/``except``: a backend throw must stay visible, and a hang
            leaves a 'start' line with no 'done' — which still localizes the
            blocking step on Windows.
            """
            if not debug:
                return call()
            logger.debug("teardown: %s start", label)
            t0 = time.monotonic()
            result = call()
            logger.debug("teardown: %s done in %.3fs", label, time.monotonic() - t0)
            return result

        _timed("stop()", player.stop)
        _timed("setVideoOutput(None)", lambda: player.setVideoOutput(None))
        player.positionChanged.disconnect(self._on_position_changed)
        player.durationChanged.disconnect(self._on_duration_changed)
        player.playbackStateChanged.disconnect(self._on_playback_state_changed)
        player.errorOccurred.disconnect(self._on_media_error)
        player.tracksChanged.disconnect(self._on_tracks_changed)
        player.mediaStatusChanged.disconnect(self._on_media_status_changed)
        player.setAudioOutput(None)
        _timed("deleteLater()", player.deleteLater)
        if debug:
            # ``deleteLater`` only QUEUES the C++ dtor; the queued delete is serviced
            # by the OUTER QApplication loop after exec() exits, so a destruction-time
            # hang otherwise surfaces as a post-dialog freeze the timing above can't
            # see. Force just this player's dtor to run synchronously here (targeted
            # receiver, not the None form conftest._drain_qt_deletes uses, so no
            # unrelated objects are destroyed). Safe: the player's signals are already
            # disconnected and it is not the object handling the current click event.
            logger.debug("teardown: drain DeferredDelete start")
            t0 = time.monotonic()
            QCoreApplication.sendPostedEvents(player, QEvent.Type.DeferredDelete.value)
            logger.debug("teardown: drain DeferredDelete done in %.3fs", time.monotonic() - t0)
        if self.audio_output is not None:
            self.audio_output.deleteLater()
        self.player = None
        self.audio_output = None
        # The armed single-shot AV1 watchdog is intentionally left running (only
        # set_source stops it): _on_av1_watchdog_timeout touches child widgets only,
        # never self.player, so a late fire after teardown is a no-op, and the timer
        # dies with the widget. Don't add _av1_watchdog.stop() here assuming oversight.
        self._av1_watchdog_pending = False

    def _join_probe_worker(self) -> None:
        """Cancel and short-bounded-join any in-flight ffprobe worker.

        Bumps the source generation first so the captured generation of any
        outstanding probe no longer matches: a result that was already emitted
        and queued before ``cancel()`` took effect is then dropped by the guard
        in :meth:`_configure_player` rather than building a player into a
        half-torn-down widget. ``set_source`` bumps again afterwards, so its own
        fresh probe still matches — the counter only ever increases, never
        colliding across calls.

        ``SingleCallWorker.cancel()`` only sets an Event checked before/after the
        ffprobe subprocess, so it cannot interrupt a probe blocked mid-call. A
        genuinely stuck probe therefore stays running past the join and is
        returned as a laggard. We DETACH each laggard from this (dying) widget
        with ``setParent(None)`` so Qt does not destroy a running QThread when the
        widget's C++ object is freed — destroying a running QThread aborts the
        process, the exact failure this hardening guards against. The detached
        worker stays in ``parent._off_thread_workers`` (keeping its Python wrapper
        alive); it finishes its ffprobe eventually, emits ``finished``, and
        self-cleans via the ``run_off_thread`` handler (registry discard +
        deleteLater), which does not touch the widget's C++ object. The generation
        guard already neutralised its late ``_configure``, so a short join is
        enough — no long GUI-thread stall is needed.
        """
        self._source_generation += 1
        laggards = join_tracked_workers(self, timeout_ms=200)
        for worker in laggards:
            worker.setParent(None)  # detach from the dying widget; self-cleans on finished
        self._probe_worker = None

    def closeEvent(self, event) -> None:
        """Tear down the multimedia backend deterministically on widget close.

        Without this, ``QMediaPlayer`` and ``QAudioOutput`` — even though now
        parented to ``self`` — might be freed after the ``QVideoWidget`` C++
        object has already been destroyed, causing a use-after-free in the
        multimedia pipeline (OVH-057 / Issue #55).

        ``_teardown_player`` also cancels/joins any in-flight ffprobe worker so a
        probe finishing after the widget is gone neither builds a player into a
        dead C++ object nor leaves a running QThread parented to the widget to be
        destroyed (and abort the process); a stuck probe is detached instead.
        """
        self._teardown_player()
        super().closeEvent(event)

    def release(self) -> None:
        """Fully tear down the player and join any in-flight probe.

        Dialogs embedding this widget call this on their exit path (``finished``
        / ``accept`` / ``reject`` / ``closeEvent``) instead of plain ``stop()``,
        because Qt does not propagate a parent dialog's close to child widgets —
        without this, a probe still running when the dialog closes outlives the
        widget; ``_teardown_player`` detaches a stuck probe so its QThread is not
        destroyed while running (which would abort the process at C++ teardown).
        """
        self._teardown_player()

    # ------------------------------------------------------------------
    # Public control API
    # ------------------------------------------------------------------

    def seek_seconds(self, seconds: float) -> None:
        """Seek to an absolute position.

        If the source's ffprobe is still in flight (player not built yet), the
        seek is queued and applied when the player is configured.

        Args:
            seconds: Target position in seconds (clamped to >= 0).
        """
        target_ms = max(0, int(seconds * 1000))
        if self.player is not None:
            self.player.setPosition(target_ms)
        else:
            self._pending_seek_ms = target_ms

    def set_offset(self, offset: float) -> None:
        """Update the subtitle timing offset (overlay sync only).

        Args:
            offset: New offset in seconds.
        """
        self._offset = offset

    def play(self) -> None:
        """Start playback.

        If the source's ffprobe is still in flight (player not built yet), the
        request is queued and honoured when the player is configured.
        """
        self._pending_pause = False
        if self.player is not None:
            self.player.play()
        else:
            self._pending_play = True

    def pause(self) -> None:
        """Pause playback.

        If the source's ffprobe is still in flight (player not built yet), the
        pause is queued and applied when the player is configured.
        """
        self._pending_play = False
        if self.player is not None:
            self.player.pause()
        else:
            self._pending_pause = True

    def stop(self) -> None:
        """Stop playback (no-op if no source has been loaded)."""
        self._pending_play = False
        self._pending_pause = False
        if self.player is not None:
            self.player.stop()

    def toggle_play_pause(self) -> None:
        """Toggle play/pause (no-op if no source has been loaded)."""
        if self.player is None:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    # ------------------------------------------------------------------
    # Internal signal handlers
    # ------------------------------------------------------------------

    def _on_tracks_changed(self) -> None:
        """Select the Japanese audio track once QMediaPlayer enumerates tracks."""
        if self.player is None:  # safety: signal only fires after set_source
            return
        if self._jp_audio_index is not None:
            # ffprobe found JP, or user gave an override — honor it
            track_count = len(self.player.audioTracks())
            if self._jp_audio_index >= track_count:
                logger.warning(
                    f"Audio track index {self._jp_audio_index} out of range "
                    f"(player reports {track_count} audio tracks)"
                )
                return
            self.player.setActiveAudioTrack(self._jp_audio_index)
            logger.info(f"Selected audio track {self._jp_audio_index} in mini-player")
            return

        # Both override and ffprobe returned nothing — try Qt-side language metadata.
        for i, track in enumerate(self.player.audioTracks()):
            lang = track.value(QMediaMetaData.Key.Language)
            if lang == QLocale.Language.Japanese:
                self.player.setActiveAudioTrack(i)
                logger.info(f"Selected Japanese audio track {i} via Qt metadata fallback")
                return
        # Qt also can't identify — leave QMediaPlayer's default (no action)

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        """Update play button text based on playback state.

        Args:
            state: Current playback state.
        """
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.play_button.setText(self.tr("Pause"))
        else:
            self.play_button.setText(self.tr("Play"))

    def _on_media_error(self, error: QMediaPlayer.Error, error_string: str) -> None:
        """Handle media player errors by showing message in subtitle label.

        Args:
            error: QMediaPlayer.Error enum value.
            error_string: Human-readable error description.
        """
        self.subtitle_label.setText(tr_format(self.tr("Video error: %1"), error_string))
        self.subtitle_label.setVisible(True)

    def _on_video_frame_changed(self) -> None:
        """Record that at least one decoded video frame has arrived from the sink.

        Connected once in _setup_ui to ``QVideoWidget.videoSink().videoFrameChanged``.
        Setting this flag prevents the AV1 watchdog from triggering the fallback UI
        when a frame is successfully decoded.

        Recovery: if the fallback notice is already showing (slow decode that
        produced its first frame only after the watchdog fired), undo the
        fallback — hide the notice, restore the video widget, and resume playback so
        the preview shows video rather than staying on the audio-only notice.
        ``play()`` is a no-op if audio is already playing and resumes it if paused.
        """
        self._got_video_frame = True
        self._av1_watchdog.stop()
        # isHidden(), not isVisible(): the explicit visibility flag set by the
        # watchdog, independent of whether the widget tree is shown on screen.
        if not self._av1_notice_label.isHidden():
            logger.info("AV1 frame decoded after the watchdog fired; restoring video preview")
            self._av1_notice_label.setVisible(False)
            self.video_widget.setVisible(True)
            if self.player is not None:
                self.player.play()

    def _nudge_first_frame(self) -> None:
        """Force the AV1 decoder to present its first frame.

        ``set_source`` only calls ``setSource``, which leaves the player in
        StoppedState at position 0. Qt's FFmpeg backend does not present a frame on
        the hardware-AV1 path until a decode is *requested*, so on a fresh open
        nothing decodes until the user interacts — and the watchdog then fires on a
        frame nobody asked for, a false 'can't decode' notice (Issue #82).

        This mirrors the proven word-click path (``WordCurationDialog._preview_scene``
        = ``seek_seconds`` then ``pause``), which decodes-and-presents reliably on the
        same machines that false-fire on open:

        - ``setPosition`` to a *real* timestamp (the first subtitle entry, else a
          few-hundred-ms fallback) — a 1 ms seek coalesces to frame 0 and decodes
          nothing.
        - ``pause()`` transitions StoppedState → PausedState, which drives the decode
          pipeline to present the frame. A bare seek on a never-played stopped player
          may not.

        The watchdog then measures a real decode attempt: a decodable source produces
        a frame and disarms it; an undecodable one never does and the watchdog
        correctly reveals the fallback.

        No audio: ``pause()`` only enters PausedState (audio flows in PlayingState),
        so the player stays silent until the user presses Play.
        """
        if self.player is not None and self._is_av1 and not self._got_video_frame:
            self.player.setPosition(self._nudge_position_ms())
            self.player.pause()

    def _nudge_position_ms(self) -> int:
        """Position (ms) for the AV1 first-frame nudge.

        Seeks to the first subtitle entry's start so the seek lands on a real
        keyframe (a genuine demux+decode), not the coalesced no-op a near-zero seek
        produces. Falls back to ``_AV1_NUDGE_FALLBACK_MS`` when there are no entries.
        """
        if self.subtitle_entries:
            return max(_AV1_NUDGE_FALLBACK_MS, int(self.subtitle_entries[0][0] * 1000))
        return _AV1_NUDGE_FALLBACK_MS

    def _arm_av1_decode_check(self) -> None:
        """Nudge a first-frame decode and start the watchdog deadline.

        Caller guarantees the widget is on screen, the source is AV1, and no frame
        has decoded yet. Nudge first so a frame is actually requested, then start
        the clock that reveals the fallback if none arrives.
        """
        self._nudge_first_frame()
        self._av1_watchdog.start(_AV1_WATCHDOG_MS)

    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        """Nudge a decode and arm the AV1 watchdog once media is loaded.

        For an AV1 source, force the first frame to decode (``_nudge_first_frame``)
        and start the watchdog: if the machine can hardware-decode AV1 the frame
        arrives and disarms the watchdog; if it genuinely cannot, no frame arrives
        and the watchdog reveals the fallback UI after the timeout.

        The nudge seeks the video sink, which only presents a frame once it is on
        screen. Both callers ``set_source`` during dialog construction, before
        ``.exec()`` shows the window, so LoadedMedia routinely fires while hidden.
        Nudging/arming then would seek into a hidden sink and expire before the
        window is composited — a false fallback (Issue #82). When hidden, defer to
        ``showEvent``.

        The watchdog is only armed when ``_is_av1`` is True; non-AV1 sources skip
        this path entirely.

        Args:
            status: The new QMediaPlayer media status.
        """
        if status in (_LOADED_MEDIA, _BUFFERED_MEDIA) and self._is_av1 and not self._got_video_frame:
            if self.isVisible():
                # Idempotent: LoadedMedia is typically followed by BufferedMedia,
                # so guard on isActive() to nudge + arm once per source rather than
                # re-seeking and restarting the clock on the second status change.
                if not self._av1_watchdog.isActive():
                    self._arm_av1_decode_check()
            else:
                self._av1_watchdog_pending = True

    def showEvent(self, event) -> None:
        """Nudge + arm a decode check deferred while the widget was hidden.

        The nudge seeks the video sink, which can only present a frame once it is
        on screen; see ``_on_media_status_changed`` (Issue #82).
        """
        super().showEvent(event)
        if (
            self._av1_watchdog_pending
            and self._is_av1
            and not self._got_video_frame
            and not self._av1_watchdog.isActive()
        ):
            self._av1_watchdog_pending = False
            self._arm_av1_decode_check()

    def _on_av1_watchdog_timeout(self) -> None:
        """Handle the AV1 watchdog firing after no decoded frame arrived.

        If ``_got_video_frame`` is still False when this fires, the AV1 video
        could not be decoded (no hardware decoder available on this machine).
        The video widget is hidden and the fallback notice shown in its place, but
        the player is left running so audio stays playable (callers such as the
        curation dialog keep it paused after seeking, so playback is on-demand, not
        automatic) and the subtitle overlay keeps updating from ``positionChanged``
        — letting the user verify audio/subtitle sync even without a video preview.
        """
        if self._is_av1 and not self._got_video_frame:
            logger.info(
                "AV1 watchdog fired — no decoded frame within the deadline; " "hiding video, keeping audio/subtitles"
            )
            self.video_widget.setVisible(False)
            self._av1_notice_label.setVisible(True)

    def _on_position_changed(self, position: int) -> None:
        """Handle media position change.

        Args:
            position: Current position in milliseconds.
        """
        if self.player is None:  # safety: signal only fires after set_source
            return
        self.position_slider.setValue(position)

        duration = self.player.duration()
        self.time_label.setText(f"{self._format_time(position)} / {self._format_time(duration)}")

        current_seconds = position / 1000.0
        self._update_subtitle(current_seconds)

    def _on_duration_changed(self, duration: int) -> None:
        """Handle media duration change.

        Args:
            duration: Total duration in milliseconds.
        """
        self.position_slider.setRange(0, duration)

    def _on_slider_moved(self, position: int) -> None:
        """Handle slider manual move.

        Args:
            position: New position in milliseconds.
        """
        if self.player is not None:
            self.player.setPosition(position)

    def _update_subtitle(self, current_seconds: float) -> None:
        """Update the subtitle label based on current playback position.

        Args:
            current_seconds: Current playback position in seconds.
        """
        for start, end, text in self.subtitle_entries:
            adjusted_start = start + self._offset
            adjusted_end = end + self._offset
            if adjusted_start <= current_seconds <= adjusted_end:
                self.subtitle_label.setText(text)
                self.subtitle_label.setVisible(True)
                return

        self.subtitle_label.setVisible(False)

    @staticmethod
    def _format_time(ms: int) -> str:
        """Format milliseconds as MM:SS.

        Args:
            ms: Time in milliseconds.

        Returns:
            Formatted time string.
        """
        if ms < 0:
            ms = 0
        total_seconds = ms // 1000
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        return f"{minutes:02d}:{seconds:02d}"
