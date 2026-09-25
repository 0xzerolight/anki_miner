"""Media extraction settings panel."""

from dataclasses import replace

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QSpinBox

from anki_miner.gui.widgets.base import FormPanel
from anki_miner.utils.i18n import tr_format

# Animated-screenshot size presets: fps, height (px), quality (0-100).
# "balanced" == today's pre-preset defaults (config.py:286-288).
ANIMATED_SIZE_PRESETS: dict[str, tuple[int, int, int]] = {
    "small": (12, 480, 30),
    "balanced": (20, 720, 30),
    "high": (24, 1080, 50),
}


class MediaSettingsPanel(FormPanel):
    """Panel for media extraction settings.

    Two sections:
    - Sentence audio: format, bitrate, padding, plus the spoken-sentence (TTS)
      combo for manga/book sources, which have no source audio of their own.
    - Screenshot: offset, plus the animated screenshot toggle and parameters.
    """

    ANCHOR_NAMESPACE = "media"

    def __init__(self, parent=None):
        """Initialize the media settings panel."""
        super().__init__(self.tr("Card Media"), parent=parent)
        self._setup_fields()

    def _setup_fields(self) -> None:
        """Set up the panel fields."""
        self.add_section(self.tr("Sentence Audio"))

        # Audio format (Issue #18)
        self.audio_format_combo = QComboBox()
        self.audio_format_combo.addItems(["mp3", "opus"])
        self.add_field(
            self.tr("Audio Format"),
            self.audio_format_combo,
            helper=self.tr(
                "MP3: universal compatibility. Opus: smaller files at equivalent quality (needs ffmpeg with libopus)."
            ),
        )

        # Audio bitrate (Issue #18)
        self.audio_bitrate_spinbox = QSpinBox()
        self.audio_bitrate_spinbox.setRange(32, 320)
        self.audio_bitrate_spinbox.setSingleStep(16)
        self.audio_bitrate_spinbox.setSuffix(self.tr(" kbps"))
        self.add_field(
            self.tr("Audio Bitrate"),
            self.audio_bitrate_spinbox,
            helper=self.tr(
                "Higher = better quality, larger files. 64-96 kbps Opus or 128-192 kbps MP3 are good defaults."
            ),
        )

        # Audio padding
        self.audio_padding_spinbox = QDoubleSpinBox()
        self.audio_padding_spinbox.setRange(0.0, 5.0)
        self.audio_padding_spinbox.setSingleStep(0.1)
        self.audio_padding_spinbox.setSuffix(self.tr(" seconds"))
        self.add_field(
            self.tr("Audio Padding"),
            self.audio_padding_spinbox,
            helper=self.tr("Extra time before and after the subtitle."),
        )

        # Sentence TTS for reading sources (manga/novels): folded from the
        # Audio page's master + two provider checkboxes (T11) into one combo.
        # The loaded (enabled, google, papago) triple is kept verbatim until
        # the user actually activates the combo (_tts_touched) — see
        # _current_reading_tts_triple.
        self._reading_tts_loaded: tuple[bool, bool, bool] = (False, True, True)
        self._tts_touched = False
        self.reading_tts_combo = QComboBox()
        self.reading_tts_combo.addItem(self.tr("Off"), "off")
        self.reading_tts_combo.addItem(self.tr("Google, then Papago"), "both")
        self.reading_tts_combo.addItem(self.tr("Google only"), "google")
        self.reading_tts_combo.addItem(self.tr("Papago only"), "papago")
        # `activated` (user-only) so a programmatic setCurrentIndex from
        # _set_reading_tts never trips the touched flag.
        self.reading_tts_combo.activated.connect(self._on_reading_tts_activated)
        self.add_field(
            self.tr("Spoken sentences for manga and books"),
            self.reading_tts_combo,
            helper=self.tr(
                "Add spoken audio to cards from manga and books, which have no source "
                "audio. Sentence text is sent to the selected online services."
            ),
            anchor="reading_tts",
            anchor_text=lambda: (
                "TTS",
                *(self.reading_tts_combo.itemText(i) for i in range(self.reading_tts_combo.count())),
            ),
        )

        self.add_section(self.tr("Screenshot"))

        # Screenshot offset
        self.screenshot_offset_spinbox = QDoubleSpinBox()
        self.screenshot_offset_spinbox.setRange(0.0, 10.0)
        self.screenshot_offset_spinbox.setSingleStep(0.1)
        self.screenshot_offset_spinbox.setSuffix(self.tr(" seconds"))
        self.add_field(
            self.tr("Screenshot Offset"),
            self.screenshot_offset_spinbox,
            helper=self.tr("Measured from the subtitle start time."),
        )

        # Animated screenshot toggle
        self.animated_checkbox = QCheckBox(self.tr("Enable animated screenshots"))
        self.animated_checkbox.setToolTip(
            self.tr(
                "Capture a short video clip instead of a static frame. "
                "Larger files, slower encode; not all Anki clients render animated AVIF/WebP."
            )
        )
        self.add_field("", self.animated_checkbox)

        # Format
        self.animated_format_combo = QComboBox()
        self.animated_format_combo.addItems(["avif", "webp"])
        self.add_field(
            self.tr("Animated Format"),
            self.animated_format_combo,
            helper=self.tr("AVIF: smaller files; WebP: broader Anki client support"),
        )

        # Match audio duration toggle
        self.animated_match_audio_checkbox = QCheckBox(self.tr("Match audio duration"))
        self.animated_match_audio_checkbox.setToolTip(
            self.tr("Animated clip spans the audio clip's time range. Overrides Clip Duration.")
        )
        self.add_field("", self.animated_match_audio_checkbox)

        # Clip duration
        self.animated_duration_spinbox = QDoubleSpinBox()
        self.animated_duration_spinbox.setRange(0.5, 10.0)
        self.animated_duration_spinbox.setSingleStep(0.5)
        self.animated_duration_spinbox.setSuffix(self.tr(" seconds"))
        self.animated_duration_spinbox.setToolTip(
            self.tr("Clip length, capped by subtitle duration. Ignored if Match audio duration is on.")
        )
        self.add_field(self.tr("Clip Duration"), self.animated_duration_spinbox)

        # Size (fps / height / quality preset)
        self._custom_animated_triple: tuple[int, int, int] = ANIMATED_SIZE_PRESETS["balanced"]
        self.animated_size_combo = QComboBox()
        self.animated_size_combo.addItem(self.tr("Small"), "small")
        self.animated_size_combo.addItem(self.tr("Balanced"), "balanced")
        self.animated_size_combo.addItem(self.tr("High"), "high")
        self.animated_size_combo.setToolTip(self.tr("Frame rate, height and quality for the animated clip."))
        self.animated_size_combo.activated.connect(self._on_animated_size_activated)
        self.add_field(
            self.tr("Size"),
            self.animated_size_combo,
            anchor_text=self._animated_size_search_text,
        )

        self.animated_checkbox.toggled.connect(self._set_animated_enabled)
        self.animated_match_audio_checkbox.toggled.connect(self._set_match_audio)
        self._set_animated_enabled(self.animated_checkbox.isChecked())

        self.add_stretch()

    def _set_animated_enabled(self, enabled: bool) -> None:
        """Enable or disable the animated screenshot sub-controls."""
        for widget in (
            self.animated_format_combo,
            self.animated_match_audio_checkbox,
            self.animated_duration_spinbox,
            self.animated_size_combo,
        ):
            widget.setEnabled(enabled)
        # Re-apply match-audio gating so the duration spinbox stays disabled
        # when match-audio is on, even after the parent feature is re-enabled.
        self._set_match_audio(self.animated_match_audio_checkbox.isChecked())

    def _set_match_audio(self, match: bool) -> None:
        """Disable the duration spinbox when match-audio overrides it.

        Only enables the spinbox when the parent animated feature is on AND
        match-audio is off; otherwise the spinbox value is irrelevant.
        """
        feature_on = self.animated_checkbox.isChecked()
        self.animated_duration_spinbox.setEnabled(feature_on and not match)

    def _on_animated_size_activated(self, index: int) -> None:
        """Drop the Custom entry once the user picks a real preset.

        Only ``activated`` (user interaction) fires this, never a
        programmatic ``setCurrentIndex`` from :meth:`_set_animated_size` —
        so loading a still-custom config never trips it. Once dropped,
        Custom cannot be re-selected.
        """
        if self.animated_size_combo.itemData(index) == "custom":
            return
        custom_index = self.animated_size_combo.findData("custom")
        if custom_index != -1:
            self.animated_size_combo.removeItem(custom_index)

    def _set_animated_size(self, fps: int, height: int, quality: int) -> None:
        """Select the preset matching ``(fps, height, quality)``, or show Custom.

        Replaces any existing Custom entry outright so a second load (a
        different profile, a config reload) never leaves a stale label
        behind.
        """
        current_custom_index = self.animated_size_combo.findData("custom")
        if current_custom_index != -1:
            self.animated_size_combo.removeItem(current_custom_index)

        triple = (fps, height, quality)
        for key, preset in ANIMATED_SIZE_PRESETS.items():
            if preset == triple:
                self.animated_size_combo.setCurrentIndex(self.animated_size_combo.findData(key))
                return

        self._custom_animated_triple = triple
        label = tr_format(self.tr("Custom (%1 fps · %2 px · quality %3)"), fps, height, quality)
        self.animated_size_combo.addItem(label, "custom")
        self.animated_size_combo.setCurrentIndex(self.animated_size_combo.count() - 1)

    def _animated_size_search_text(self) -> tuple[str, ...]:
        """Searchable text for ``animated_size_combo``: every item plus its tooltip.

        "fps" is a plain English keyword, not a translated string: the presets
        (Small/Balanced/High) never spell it out, only the widget's own tooltip
        says "Frame rate", and a still-custom entry's "fps" label only exists
        once a config has saved a non-preset triple.
        """
        parts: list[str] = ["fps"]
        combo = self.animated_size_combo
        for index in range(combo.count()):
            parts.append(combo.itemText(index))
            tooltip = combo.itemData(index, Qt.ItemDataRole.ToolTipRole)
            if tooltip:
                parts.append(str(tooltip))
        return tuple(parts)

    def _current_animated_size_triple(self) -> tuple[int, int, int]:
        """Return the (fps, height, quality) triple for the selected entry."""
        key = self.animated_size_combo.currentData()
        if key == "custom":
            return self._custom_animated_triple
        return ANIMATED_SIZE_PRESETS[key]

    def _on_reading_tts_activated(self, _index: int) -> None:
        """Mark the combo user-touched; only past this point does contribute() write it."""
        self._tts_touched = True

    def _set_reading_tts(self, enabled: bool, google: bool, papago: bool) -> None:
        """Load the three reading_tts_* config bools into the combo (no signals)."""
        self._reading_tts_loaded = (enabled, google, papago)
        self._tts_touched = False
        if not enabled or not (google or papago):
            key = "off"
        elif google and papago:
            key = "both"
        elif google:
            key = "google"
        else:
            key = "papago"
        self.reading_tts_combo.blockSignals(True)
        try:
            self.reading_tts_combo.setCurrentIndex(self.reading_tts_combo.findData(key))
        finally:
            self.reading_tts_combo.blockSignals(False)

    def _current_reading_tts_triple(self) -> tuple[bool, bool, bool]:
        """Return the (enabled, google, papago) triple this panel contributes.

        Unchanged from what was loaded until the user actually activates the
        combo — an untouched combo must never rewrite the provider pair it was
        seeded with, even past an edit to another field on this panel. Past
        that point: Off writes ``enabled=False`` with the providers as loaded
        (the pair a later re-enable would resume with); any other item writes
        ``enabled=True`` plus its own provider pair.
        """
        if not self._tts_touched:
            return self._reading_tts_loaded
        key = self.reading_tts_combo.currentData()
        _enabled, google, papago = self._reading_tts_loaded
        if key == "both":
            return (True, True, True)
        if key == "google":
            return (True, True, False)
        if key == "papago":
            return (True, False, True)
        return (False, google, papago)  # "off"

    # ------------------------------------------------------------------
    # Accessors (config <-> widget conversion)
    # ------------------------------------------------------------------

    def get_audio_format(self) -> str:
        """Return the selected audio format."""
        return self.audio_format_combo.currentText()

    def set_audio_format(self, value: str) -> None:
        """Set the audio format combo."""
        self.audio_format_combo.setCurrentText(value)

    def get_audio_bitrate(self) -> int:
        """Return the audio bitrate (kbps)."""
        return self.audio_bitrate_spinbox.value()

    def set_audio_bitrate(self, value: int) -> None:
        """Set the audio bitrate spinbox."""
        self.audio_bitrate_spinbox.setValue(value)

    def get_audio_padding(self) -> float:
        """Return the audio padding (seconds)."""
        return self.audio_padding_spinbox.value()

    def set_audio_padding(self, value: float) -> None:
        """Set the audio padding spinbox."""
        self.audio_padding_spinbox.setValue(value)

    def get_screenshot_offset(self) -> float:
        """Return the screenshot offset (seconds)."""
        return self.screenshot_offset_spinbox.value()

    def set_screenshot_offset(self, value: float) -> None:
        """Set the screenshot offset spinbox."""
        self.screenshot_offset_spinbox.setValue(value)

    def get_screenshot_animated(self) -> bool:
        """Return whether animated screenshots are enabled."""
        return self.animated_checkbox.isChecked()

    def set_screenshot_animated(self, value: bool) -> None:
        """Set the animated screenshots checkbox and update dependent widgets."""
        self.animated_checkbox.setChecked(value)
        self._set_animated_enabled(value)

    def get_screenshot_animated_format(self) -> str:
        """Return the animated screenshot format."""
        return self.animated_format_combo.currentText()

    def set_screenshot_animated_format(self, value: str) -> None:
        """Set the animated screenshot format combo."""
        self.animated_format_combo.setCurrentText(value)

    def get_screenshot_animated_clip_duration(self) -> float:
        """Return the animated clip duration (seconds)."""
        return self.animated_duration_spinbox.value()

    def set_screenshot_animated_clip_duration(self, value: float) -> None:
        """Set the animated clip duration spinbox."""
        self.animated_duration_spinbox.setValue(value)

    def get_screenshot_animated_match_audio(self) -> bool:
        """Return whether match-audio duration is enabled."""
        return self.animated_match_audio_checkbox.isChecked()

    def set_screenshot_animated_match_audio(self, value: bool) -> None:
        """Set the match-audio checkbox and update dependent widgets."""
        self.animated_match_audio_checkbox.setChecked(value)
        self._set_match_audio(value)

    # ------------------------------------------------------------------
    # Config marshalling contract (OVH-019)
    # ------------------------------------------------------------------

    def load_from_config(self, config) -> None:
        """Populate all widgets from ``config``.

        Called by :meth:`SettingsTab._load_config` as part of the panel loop.
        """
        self.set_audio_format(config.audio_format)
        self.set_audio_bitrate(config.audio_bitrate)
        self.set_audio_padding(config.audio_padding)
        self._set_reading_tts(
            config.reading_tts_enabled,
            config.reading_tts_google_enabled,
            config.reading_tts_papago_enabled,
        )
        self.set_screenshot_offset(config.screenshot_offset)
        self.set_screenshot_animated(config.screenshot_animated)
        self.set_screenshot_animated_format(config.screenshot_animated_format)
        self.set_screenshot_animated_clip_duration(config.screenshot_animated_clip_duration)
        self.set_screenshot_animated_match_audio(config.screenshot_animated_match_audio)
        self._set_animated_size(
            config.screenshot_animated_fps,
            config.screenshot_animated_height,
            config.screenshot_animated_quality,
        )

    def contribute(self, config):
        """Return a new config with this panel's fields applied.

        Uses ``dataclasses.replace`` so the frozen-config invariant is preserved.
        Called by :meth:`SettingsTab.commit_settings` as part of the contribute fold.
        """
        fps, height, quality = self._current_animated_size_triple()
        reading_tts_enabled, reading_tts_google_enabled, reading_tts_papago_enabled = self._current_reading_tts_triple()
        return replace(
            config,
            audio_format=self.get_audio_format(),
            audio_bitrate=self.get_audio_bitrate(),
            audio_padding=self.get_audio_padding(),
            reading_tts_enabled=reading_tts_enabled,
            reading_tts_google_enabled=reading_tts_google_enabled,
            reading_tts_papago_enabled=reading_tts_papago_enabled,
            screenshot_offset=self.get_screenshot_offset(),
            screenshot_animated=self.get_screenshot_animated(),
            screenshot_animated_format=self.get_screenshot_animated_format(),
            screenshot_animated_clip_duration=self.get_screenshot_animated_clip_duration(),
            screenshot_animated_match_audio=self.get_screenshot_animated_match_audio(),
            screenshot_animated_fps=fps,
            screenshot_animated_height=height,
            screenshot_animated_quality=quality,
        )
