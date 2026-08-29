"""Reusable video player widget with a subtitle strip (embedded libmpv backend).

Replaced QMediaPlayer/QtMultimedia in the mpv migration: the Qt FFmpeg backend
carried a whole bug family (Windows D3D-sink teardown freeze, no software AV1
decode → the deleted Issue #82 watchdog/nudge apparatus). libmpv decodes AV1 in
software (dav1d, ``hwdec=no``) and uses a bounded ``terminate()`` wrapper, so
none of that machinery exists anymore.

Ownership: this controller owns the ``mpv.MPV`` handle (``self.player``, None
until the first ``set_source``); :class:`MpvVideoWidget` owns only the render
context. One MPV instance per widget lifetime — re-sourcing goes through
``loadfile`` on the same instance, and ``terminate()`` runs exactly once in
``_teardown_player`` (the fewer detach→terminate transitions, the smaller the
window for the libmpv render-context/terminate ordering abort).

Threading: python-mpv property observers and event callbacks fire on its event
thread. Every observer/callback here does exactly one thing — emit an
``object``-typed Qt signal (queued to the GUI thread). mpv properties are
nullable and ``observe_property`` fires an immediate initial callback with the
current value (None before a file loads), so every slot None-guards: a None
duration/time-pos/eof is the NORMAL first event, not an edge case.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils import video_preview
from anki_miner.gui.utils.content_text import apply_content_font
from anki_miner.gui.utils.fonts import (
    JAPANESE_FEATURE,
    apply_japanese_block_format,
    japanese_line_spacing,
)
from anki_miner.gui.widgets.mpv_video_widget import MpvVideoWidget
from anki_miner.languages.profile import ContentTextStyle
from anki_miner.languages.registry import get_profile
from anki_miner.utils.audio_track_detector import JAPANESE_LANGUAGE_CODES, matches_language_tag
from anki_miner.utils.bundled_binary import frozen_state
from anki_miner.utils.i18n import tr_format
from anki_miner.utils.mpv_loader import create_mpv_player, mpv_available, terminate_mpv_player

logger = logging.getLogger(__name__)

# Vertical padding inside the subtitle strip, added on top of the two reserved
# lines. Matches the padding the stylesheet gives the strip.
_STRIP_PADDING_Y = SPACING.xxs

# mpv end-file reason code for "stopped due to playback error" (render.h /
# client.h: MPV_END_FILE_REASON_ERROR). Kept as a literal so this module never
# needs the mpv module itself at import time.
_END_FILE_REASON_ERROR = 4


class SubtitlePlayerWidget(QWidget):
    """Reusable video player with a fixed two-line subtitle strip.

    Owns MpvVideoWidget, the subtitle strip, position QSlider, time label,
    play/pause button, and the mpv.MPV handle. No player exists until
    set_source is called.
    """

    #: Emitted on the GUI thread once mpv has the file open, i.e. once seeking
    #: is safe and playback can start. Consumers hang their "Loading…" state off
    #: this rather than guessing from a timer.
    source_loaded = pyqtSignal()

    #: Emitted on the GUI thread when mpv gave up on the file. Payload is the
    #: human-readable reason. Distinct from ``render_failed`` on the video
    #: widget: that one still plays audio, this one plays nothing.
    playback_failed = pyqtSignal(str)

    #: Emitted on the GUI thread when a :meth:`play_range` preview stops being
    #: in effect — it reached its end, or any other transport action cancelled
    #: it. Consumers use it to put a "playing" button back to its idle glyph.
    range_finished = pyqtSignal()

    # Marshalling signals: emitted from python-mpv's event thread, delivered
    # queued on the GUI thread. object-typed on purpose — mpv properties are
    # nullable (see module docstring).
    _mpv_time_pos = pyqtSignal(object)
    _mpv_duration = pyqtSignal(object)
    _mpv_pause = pyqtSignal(object)
    _mpv_eof = pyqtSignal(object)
    _mpv_file_loaded = pyqtSignal()
    _mpv_playback_error = pyqtSignal(str)

    def __init__(self, parent=None, *, content_style: ContentTextStyle | None = None):
        """Initialize the player widget (no media until set_source is called).

        Args:
            parent: Optional parent widget.
            content_style: Typography for the subtitle strip, from the mining
                language's profile. ``None`` keeps today's Japanese face.
        """
        super().__init__(parent)

        # The cue line is mined content, not chrome (decision D45-B).
        self._content_style = content_style or get_profile("ja").content_style

        # Will be populated by set_source
        self.subtitle_entries: list[tuple[float, float, str]] = []
        self._offset: float = 0.0
        self._audio_track_override: int | None = None
        # Mining-language audio-track codes for auto-selection. Set by the
        # owner (SubtitleViewer / WordCurationDialog) when it holds a config;
        # this widget is constructed without one, so ja is the default.
        self.audio_track_codes: frozenset[str] = JAPANESE_LANGUAGE_CODES

        # Player is None until the first set_source call; one instance per
        # widget lifetime afterwards (loadfile per source).
        self.player: Any = None

        self._mpv_available: bool = mpv_available()
        # Warn-level mpv log dedup (Issue #100: libmpv_render can emit the
        # same GL error per frame — hundreds of identical lines). Guarded by
        # a lock: _on_mpv_log runs on python-mpv's log thread.
        self._mpv_log_lock = threading.Lock()
        self._mpv_log_last: dict[tuple[str, str, str], float] = {}
        self._mpv_log_suppressed: dict[tuple[str, str, str], int] = {}
        # Set on mpv's file-loaded event; gates seeks (mpv errors on a seek
        # before the file is loaded) and audio-track selection.
        self._file_loaded: bool = False
        # A seek issued before file-loaded is remembered here and applied in
        # _on_file_loaded — WordCurationDialog's seek-then-pause preview is
        # issued immediately after set_source, before mpv finishes loading.
        self._pending_seek_ms: int | None = None
        self._duration_ms: int = 0
        # True while mpv sits at end-of-file (keep-open=yes auto-pauses there;
        # unpausing at EOF is a no-op, so play() must seek to 0 first).
        self._at_eof: bool = False
        # loadfile issued before the render context exists is remembered here
        # and flushed on render_ready/render_failed. LOAD-BEARING: loading
        # earlier makes mpv's video-out init fail permanently for that file
        # ("vo/libmpv: No render context set." -> audio-only black pane), and
        # both consumer dialogs call set_source in __init__, before the widget
        # is shown and GL exists.
        self._pending_load: str | None = None
        # End of an in-progress play_range, in seconds; None when playback is
        # not bounded. Cleared by cancel_range, which EVERY other transport
        # entry point calls — a manual Play or a seek elsewhere must not be
        # yanked to a stop at a boundary the user has already left behind.
        self._range_end: float | None = None

        self._mpv_time_pos.connect(self._on_time_pos)
        self._mpv_duration.connect(self._on_duration)
        self._mpv_pause.connect(self._on_pause_changed)
        self._mpv_eof.connect(self._on_eof)
        self._mpv_file_loaded.connect(self._on_file_loaded)
        self._mpv_playback_error.connect(self._on_playback_error)

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the player UI layout."""
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Video widget (render-API view; owns only the mpv render context).
        #
        # THE ONE CONSTRUCTION SITE, and the only gate protecting it. Building a
        # QOpenGLWidget brings up a real GL context, and on a host whose GL
        # driver cannot load cleanly that ABORTS the process — no Python
        # traceback, nothing catchable. A field report on Arch + AppImage died
        # inside QOpenGLWidget.__init__ on every video mining run, because the
        # curator is on the mandatory path for all of them.
        #
        # Gating HERE rather than at WordCurationDialog._show_player is
        # deliberate: it covers both consumers (curator and subtitle viewer) and
        # leaves _side_key alone, so every saved splitter layout still restores.
        #
        # It also subsumes the old libmpv-absent path, which used to build the
        # GL widget anyway just to render a text notice over it.
        self._video_surface = video_preview.preview_enabled() and self._mpv_available
        self.video_widget: MpvVideoWidget | None = None
        if self._video_surface:
            self.video_widget = MpvVideoWidget()
            self.video_widget.render_ready.connect(self._on_render_ready)
            self.video_widget.render_failed.connect(self._on_render_failed)
            layout.addWidget(self.video_widget, 1)

        # Backend-degradation notice — hidden by default. Two texts share it:
        # libmpv absent entirely (set_source shows it and no player is built),
        # or rendering impossible on this display (render_failed; audio still
        # plays). Never both video and notice at once.
        self._backend_notice_label = QLabel()
        self._backend_notice_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._backend_notice_label.setWordWrap(True)
        self._backend_notice_label.setVisible(False)
        layout.addWidget(self._backend_notice_label)

        # Subtitle strip — two lines, always allocated (decision D45-B).
        #
        # It used to be shown and hidden per cue, so the video jumped every time
        # a line appeared, and jumped again when a long line wrapped onto a
        # second one. The strip now reserves two lines of Japanese content for
        # the whole session and clears its text between cues instead of
        # disappearing, so nothing below it ever moves.
        #
        # It is a document rather than a label because Japanese content gets
        # looser leading and Qt stylesheets ignore `line-height`. It takes no
        # focus and no text interaction, so Space still reaches the player's
        # play/pause shortcut when the pointer is over the strip.
        self.subtitle_strip = QTextEdit()
        self.subtitle_strip.setObjectName("subtitle-strip")
        self.subtitle_strip.setReadOnly(True)
        self.subtitle_strip.setFrameShape(QFrame.Shape.NoFrame)
        self.subtitle_strip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.subtitle_strip.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.subtitle_strip.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.subtitle_strip.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.subtitle_strip.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.subtitle_strip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        apply_content_font(self.subtitle_strip, self._content_style, role=JAPANESE_FEATURE)
        strip_height = 2 * japanese_line_spacing(JAPANESE_FEATURE) + 2 * _STRIP_PADDING_Y
        self.subtitle_strip.setFixedHeight(strip_height)
        self._set_cue_text("")
        layout.addWidget(self.subtitle_strip)

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
    ) -> None:
        """Load a video source and configure subtitle playback.

        Creates the mpv player on first call; later calls reuse it via
        ``loadfile`` (paused, from position 0). Audio-track selection happens
        on mpv's ``file-loaded`` event from its ``track-list`` property — the
        old off-thread ffprobe probe (and its ``ffprobe_cmd`` parameter) is
        gone.

        Args:
            video_path: Path to the video file.
            subtitle_entries: List of (start_seconds, end_seconds, text) tuples.
            offset: Initial subtitle timing offset in seconds.
            audio_track_override: Optional 0-indexed audio track (position
                within the audio-only track list, as produced by
                ``list_audio_streams``) to force instead of auto-detecting
                Japanese. None preserves auto-detect via mpv track metadata.
        """
        self.subtitle_entries = subtitle_entries
        self._offset = offset
        self._audio_track_override = audio_track_override

        # New source: nothing loaded yet, previous pendings are void.
        self._file_loaded = False
        self._pending_seek_ms = None
        self._at_eof = False
        self._set_cue_text("")

        if not self._mpv_available:
            # libmpv could not load: degrade to a notice; the rest of the dialog
            # (sentence picker, offset controls) still works. The advice is
            # platform-aware — see _backend_unavailable_text.
            self._backend_notice_label.setText(self._backend_unavailable_text())
            self._backend_notice_label.setVisible(True)
            return

        if self.video_widget is None:
            # Preview suppressed, but libmpv loaded: keep AUDIO. The abort this
            # guards against is in GL bring-up, not in libmpv, so a vo=null core
            # still plays the line — which is the whole point of the subtitle
            # viewer and half the point of the curator's Space shortcut.
            self._backend_notice_label.setText(self._preview_suppressed_text())
            self._backend_notice_label.setVisible(True)
        else:
            self._backend_notice_label.setVisible(False)
            self.video_widget.setVisible(True)

        if self.player is None:
            self.player = create_mpv_player(
                log_handler=self._on_mpv_log,
                video=self.video_widget is not None,
            )
            self._register_mpv_callbacks(self.player)
            if self.video_widget is not None:
                self.video_widget.attach(self.player)

        # Re-source parity with the old backend: a new source always starts
        # paused at 0 (the factory sets pause=True only at construction).
        self.player.pause = True
        self._load_or_defer(str(video_path))

    def _backend_unavailable_text(self) -> str:
        """Return the libmpv-unavailable notice, platform- and frozen-aware.

        On a frozen **Windows** build the bundled ``libmpv-2.dll`` is present but
        failed to load (a missing transitive dependency on this machine, e.g. an
        absent ``vulkan-1.dll``); no package-manager rescue exists, so pointing
        the user at a "install libmpv2 / brew install mpv" step is useless — send
        them to a reinstall/report path and name the log. For pip/dev installs
        (not frozen) on any OS, and for frozen **macOS/Linux** — where the
        loader's fall-through to a system libmpv means ``brew install mpv`` / the
        distro ``libmpv2`` package genuinely restores the preview — keep the
        original install advice unchanged.
        """
        frozen, _ = frozen_state()
        if frozen and sys.platform == "win32":
            return self.tr(
                "Video preview is unavailable: the bundled video component (libmpv) "
                "could not be loaded on this PC. Try reinstalling Anki Miner; if the "
                "problem persists, report it and attach your log from "
                "%USERPROFILE%\\.anki_miner\\anki_miner.log."
            )
        return self.tr(
            "Video preview requires mpv (libmpv). Bundled builds include it; "
            "on Linux install it from your package manager (e.g. libmpv2), "
            "on macOS via Homebrew (brew install mpv)."
        )

    def _preview_suppressed_text(self) -> str:
        """Return the notice for a preview that was turned off, not one that broke.

        Names the env var, which is the only thing that can suppress it: there
        is deliberately no Settings checkbox, so pointing at one would send the
        user in circles.
        """
        return self.tr("Video preview is turned off by %1. Audio still plays.").replace("%1", video_preview.ENV_VAR)

    def _load_or_defer(self, path: str) -> None:
        """Issue loadfile now, or queue it until the render context exists.

        Loading before the render context exists makes mpv's video-out init
        fail permanently for that file ("vo/libmpv: No render context set."):
        mpv then plays audio-only into a black pane. Consumers call set_source
        during dialog __init__ — before the widget is shown and GL exists — so
        the normal first-load path is the deferred one; render_ready (or
        render_failed, where audio-only is the promised degradation) flushes it.
        """
        # No video surface means no render context will ever exist, so deferring
        # would park the load forever — load immediately for the audio-only path.
        if self.video_widget is None or self.video_widget.has_render_context:
            self.player.loadfile(path)
        else:
            self._pending_load = path

    def _flush_pending_load(self) -> None:
        if self.player is None or self._pending_load is None:
            return
        pending, self._pending_load = self._pending_load, None
        self.player.loadfile(pending)

    def _on_render_ready(self) -> None:
        """Render context is live — safe to load the queued source."""
        self._flush_pending_load()

    def _register_mpv_callbacks(self, player: Any) -> None:
        """Wire mpv observers/events to the queued marshalling signals.

        Runs once per player (= once per widget lifetime). Every handler body
        is a bare signal emit — they run on python-mpv's event thread where
        touching widgets or calling back into libmpv is undefined behavior.
        python-mpv keeps references to registered handlers; a successful
        terminate joins the event thread so no emit can outlive the widget.
        """
        player.observe_property("time-pos", lambda _name, value: self._mpv_time_pos.emit(value))
        player.observe_property("duration", lambda _name, value: self._mpv_duration.emit(value))
        player.observe_property("pause", lambda _name, value: self._mpv_pause.emit(value))
        player.observe_property("eof-reached", lambda _name, value: self._mpv_eof.emit(value))

        @player.event_callback("file-loaded")
        def _on_file_loaded_event(_event: Any) -> None:
            self._mpv_file_loaded.emit()

        @player.event_callback("end-file")
        def _on_end_file_event(event: Any) -> None:
            data = getattr(event, "data", None)
            if data is not None and getattr(data, "reason", None) == _END_FILE_REASON_ERROR:
                self._mpv_playback_error.emit(self.tr("playback failed"))

    def _teardown_player(self) -> None:
        """Detach and request bounded mpv core termination. Idempotent.

        Exact order matters (this replaces the old QMediaPlayer D3D teardown
        dance wholesale):

        1. Swap ``self.player`` to None while holding a local strong ref —
           slots guard on ``self.player is None`` from this point on.
        2. ``video_widget.detach()`` frees the render context while the core
           is still alive. Freeing against a dead core — or terminating a core
           with a live render context — is a hard process abort in libmpv.
        3. The bounded terminate helper destroys the handle and joins
           python-mpv's event thread when teardown completes in time.
        """
        if self.player is None:
            return
        player, self.player = self.player, None
        self._file_loaded = False
        self._pending_seek_ms = None
        self._pending_load = None
        if self.video_widget is not None:
            self.video_widget.detach()
        terminate_mpv_player(player)

    def closeEvent(self, event) -> None:
        """Tear down the mpv core with a bounded wait on widget close."""
        self._teardown_player()
        super().closeEvent(event)

    def release(self) -> None:
        """Fully tear down the player.

        Dialogs embedding this widget call this on their exit path
        (``finished`` / ``accept`` / ``reject`` / ``closeEvent``) because Qt
        does not propagate a parent dialog's close to child widgets.
        """
        self._teardown_player()

    # ------------------------------------------------------------------
    # Public control API
    # ------------------------------------------------------------------

    @property
    def backend_available(self) -> bool:
        """True when libmpv loaded, i.e. a player can exist at all.

        Consumers use it to tell "still loading" apart from "there was never
        going to be a picture" — the widget already explains the second case in
        its own notice, so a consumer must not narrate it a second time.
        """
        return self._mpv_available

    @property
    def video_surface_available(self) -> bool:
        """True when a GL video surface exists, i.e. a picture is possible.

        Strictly narrower than :attr:`backend_available`: audio-only is a
        supported state (preview turned off by setting or env), and a consumer
        that conflates the two would hide controls that still work.
        """
        return self.video_widget is not None

    def seek_seconds(self, seconds: float) -> None:
        """Seek to an absolute position.

        mpv errors on a seek before the file is loaded, so a seek issued
        between set_source and the file-loaded event is queued and applied in
        _on_file_loaded (WordCurationDialog seeks immediately after
        set_source).

        Args:
            seconds: Target position in seconds (clamped to >= 0).
        """
        self.cancel_range()
        target_ms = max(0, int(seconds * 1000))
        if self.player is None or not self._file_loaded:
            self._pending_seek_ms = target_ms
            return
        self._seek_ms(target_ms)

    def _seek_ms(self, target_ms: int) -> None:
        """Issue an exact absolute seek (parity with QMediaPlayer.setPosition)."""
        self.player.command("seek", target_ms / 1000.0, "absolute+exact")

    def set_offset(self, offset: float) -> None:
        """Update the subtitle timing offset (overlay sync only).

        Args:
            offset: New offset in seconds.
        """
        self._offset = offset

    def play(self) -> None:
        """Start playback (no-op if no source has been loaded).

        At end-of-file, keep-open=yes leaves mpv paused on the last frame and
        a bare unpause is a no-op — seek to 0 first so Play replays from the
        start (QMediaPlayer end-of-media parity).
        """
        self.cancel_range()
        if self.player is None:
            return
        if self._at_eof and self._file_loaded:
            self._seek_ms(0)
        self.player.pause = False

    def pause(self) -> None:
        """Pause playback (no-op if no source has been loaded)."""
        self.cancel_range()
        if self.player is None:
            return
        self.player.pause = True

    def play_range(self, start: float, end: float) -> None:
        """Play ``start`` → ``end`` (seconds), then pause at ``end``.

        Backs the word curator's audio clip preview: the user hears exactly the
        window the card will get. The stop is driven by the ``time_pos``
        observer, so it lands within one tick (~40 ms) of ``end`` — inaudible
        for a preview, and far cheaper than decoding the clip for real.

        Any subsequent transport action — Play, Pause, Stop, a seek, the Space
        shortcut — cancels the pending stop, so a user who takes over playback
        is never yanked to a halt at a boundary they have moved past. That is
        also why ``_range_end`` is assigned LAST: the seek and play below both
        route through :meth:`cancel_range`.
        """
        if self.player is None:
            return
        self.seek_seconds(start)
        self.play()
        self._range_end = end

    def cancel_range(self) -> None:
        """Drop any pending :meth:`play_range` stop (idempotent)."""
        if self._range_end is None:
            return
        self._range_end = None
        self.range_finished.emit()

    def stop(self) -> None:
        """Stop playback: paused at position 0, media kept loaded.

        NOT mpv's ``stop`` command (which unloads the file) — QMediaPlayer's
        stop kept the media, and callers re-play after stopping.
        """
        if self.player is None:
            return
        self.player.pause = True
        self.seek_seconds(0.0)

    def toggle_play_pause(self) -> None:
        """Toggle play/pause (no-op if no source has been loaded)."""
        if self.player is None:
            return
        if self.player.pause:
            self.play()
        else:
            self.pause()

    # ------------------------------------------------------------------
    # GUI-thread slots (fed by the queued marshalling signals)
    # ------------------------------------------------------------------

    def _on_file_loaded(self) -> None:
        """Select the audio track and apply a queued seek once mpv loaded the file."""
        if self.player is None:
            return
        self._file_loaded = True
        self._at_eof = False
        self._select_audio_track()
        if self._pending_seek_ms is not None:
            pending, self._pending_seek_ms = self._pending_seek_ms, None
            self._seek_ms(pending)
        # Announced last: a consumer reacting to this must find the player in
        # its final loaded state, queued seek included.
        self.source_loaded.emit()

    def _select_audio_track(self) -> None:
        """Pick the audio track from mpv's track-list.

        Override (0-based index within the audio-only track list, demuxer
        order — the same order ffprobe reports) maps to mpv's 1-based ``aid``.
        Without an override, scan track metadata for a Japanese language tag.
        Otherwise leave mpv's own default selection.
        """
        track_list = self.player.track_list or []
        audio_tracks = [t for t in track_list if t.get("type") == "audio"]

        if self._audio_track_override is not None:
            if self._audio_track_override >= len(audio_tracks):
                logger.warning(
                    "Audio track index %d out of range (player reports %d audio tracks)",
                    self._audio_track_override,
                    len(audio_tracks),
                )
                return
            self.player.aid = self._audio_track_override + 1
            logger.info("Selected audio track %d in mini-player", self._audio_track_override)
            return

        for position, track in enumerate(audio_tracks):
            lang = (track.get("lang") or "").lower()
            if matches_language_tag(lang, self.audio_track_codes):
                self.player.aid = track.get("id", position + 1)
                logger.info("Selected Japanese audio track %d via mpv track metadata", position)
                return
        # No JP tag anywhere — leave mpv's default selection.

    def _on_time_pos(self, value: object) -> None:
        """Playback position observer (seconds float; None while idle)."""
        if value is None or self.player is None:
            return
        # End of a bounded clip preview. pause() clears the range and emits
        # range_finished, so the stop happens exactly once; the rest of this
        # slot still runs so the strip paints the final position.
        if self._range_end is not None and float(value) >= self._range_end:  # type: ignore[arg-type]
            self.pause()
        position_ms = int(float(value) * 1000)  # type: ignore[arg-type]
        # Don't fight the user mid-drag: while the handle is held down, a
        # playback-driven setValue yanks it back and the scrub tugs-of-war.
        # The user's own drag drives position via _on_slider_moved.
        if not self.position_slider.isSliderDown():
            self.position_slider.setValue(position_ms)
        self.time_label.setText(f"{self._format_time(position_ms)} / {self._format_time(self._duration_ms)}")
        self._update_subtitle(float(value))  # type: ignore[arg-type]

    def _on_duration(self, value: object) -> None:
        """Duration observer (seconds float; None fires before a file loads)."""
        if value is None:
            return
        self._duration_ms = int(float(value) * 1000)  # type: ignore[arg-type]
        self.position_slider.setRange(0, self._duration_ms)

    def _on_pause_changed(self, value: object) -> None:
        """Pause observer — drives the play button label.

        keep-open=yes flips pause to True at EOF automatically, which resets
        the label to "Play" exactly like the old EndOfMedia handling.
        """
        paused = bool(value) if value is not None else True
        self.play_button.setText(self.tr("Play") if paused else self.tr("Pause"))

    def _on_eof(self, value: object) -> None:
        """eof-reached observer (bool; None while idle)."""
        self._at_eof = bool(value)
        if self._at_eof:
            self.cancel_range()

    def _on_playback_error(self, message: str) -> None:
        """Surface a playback error in the subtitle strip (old-backend parity)."""
        self._set_cue_text(tr_format(self.tr("Video error: %1"), message))
        self.playback_failed.emit(message)

    def _on_render_failed(self, reason: str) -> None:
        """mpv loaded but rendering is impossible on this display (broken GL).

        Audio and the subtitle overlay keep working — say so instead of
        leaving a silent black box.
        """
        logger.warning("Video render unavailable: %s", reason)
        self._backend_notice_label.setText(
            self.tr("Video preview is unavailable on this display. Audio and subtitles still play.")
        )
        self._backend_notice_label.setVisible(True)
        if self.video_widget is not None:
            self.video_widget.setVisible(False)
        # Honour the "audio still plays" promise: a source queued behind the
        # (failed) render context still loads — audio + overlay work without it.
        self._flush_pending_load()

    # Identical warn-level mpv lines within this window collapse into one
    # record carrying a repeat count on the next allowed emit.
    _MPV_LOG_DEDUP_WINDOW_S = 10.0

    # mpv's gl_check_error reports whatever glGetError has queued, which on the
    # first check of a render includes any error left behind by earlier setup.
    # Measured (Mesa 4.6, Wayland, libmpv 2.5): it fires exactly once per video
    # load, on plane 0 only -- planes 1 and 2 come back clean -- so it is a
    # drained leftover, not a failure to create that texture. Reproduced
    # identically on 8-bit yuv420p and 10-bit yuv420p10, and unchanged when the
    # widget is given an explicit 3.3 CoreProfile format, which rules out both
    # the 10-bit GL_R16 and the context-profile explanations. Video renders.
    # DEBUG, not WARNING: a genuinely broken render surfaces through
    # render_failed and the "audio still plays" notice, not through this line.
    _BENIGN_MPV_ERRORS = frozenset({("libmpv_render", "after creating texture: OpenGL error INVALID_ENUM.")})

    def _on_mpv_log(self, level: str, component: str, message: str) -> None:
        """Forward mpv warn/error log lines to the app logger, deduplicated.

        Runs on python-mpv's log thread — logging is thread-safe, widgets are
        not; never touch UI here. mpv can emit the same render error per frame
        (Issue #100 log spam), so identical warn-level lines are suppressed
        within ``_MPV_LOG_DEDUP_WINDOW_S`` and summarized on the next emit.
        """
        text = message.strip()
        if level not in ("fatal", "error") or (component, text) in self._BENIGN_MPV_ERRORS:
            logger.debug("mpv [%s] %s: %s", level, component, text)
            return

        key = (level, component, text)
        now = time.monotonic()
        with self._mpv_log_lock:
            last = self._mpv_log_last.get(key)
            if last is not None and now - last < self._MPV_LOG_DEDUP_WINDOW_S:
                self._mpv_log_suppressed[key] = self._mpv_log_suppressed.get(key, 0) + 1
                return
            self._mpv_log_last[key] = now
            suppressed = self._mpv_log_suppressed.pop(key, 0)
        if suppressed:
            logger.warning("mpv [%s] %s: %s (repeated %d times)", level, component, text, suppressed)
        else:
            logger.warning("mpv [%s] %s: %s", level, component, text)

    def _on_slider_moved(self, position: int) -> None:
        """Handle slider manual move.

        Args:
            position: New position in milliseconds.
        """
        self.seek_seconds(position / 1000.0)

    def _update_subtitle(self, current_seconds: float) -> None:
        """Update the subtitle label based on current playback position.

        Args:
            current_seconds: Current playback position in seconds.
        """
        for start, end, text in self.subtitle_entries:
            adjusted_start = start + self._offset
            adjusted_end = end + self._offset
            if adjusted_start <= current_seconds <= adjusted_end:
                self._set_cue_text(text)
                return

        # Cleared, never hidden: hiding the strip would move the video.
        self._set_cue_text("")

    def _set_cue_text(self, text: str) -> None:
        """Put one cue in the strip, plain text, with the Japanese leading.

        ``_update_subtitle`` runs on every position tick, so an unchanged cue
        returns immediately: rebuilding the document several times a second for
        the same line would be pure churn.

        ``setPlainText`` replaces every block, so the block format has to be
        reapplied after each *real* change or the leading survives only the
        first cue.
        """
        if self.subtitle_strip.toPlainText() == text:
            return
        self.subtitle_strip.setPlainText(text)
        apply_japanese_block_format(self.subtitle_strip.document())
        self.subtitle_strip.setAlignment(Qt.AlignmentFlag.AlignCenter)

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
