"""Media extraction settings panel."""

from PyQt6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QSpinBox

from anki_miner.gui.widgets.base import FormPanel


class MediaSettingsPanel(FormPanel):
    """Panel for media extraction settings.

    Provides:
    - Audio padding configuration
    - Screenshot offset configuration
    - Max parallel workers configuration
    - Animated screenshot toggle and parameters
    """

    def __init__(self, parent=None):
        """Initialize the media settings panel."""
        super().__init__("Media Extraction Settings", parent=parent)
        self._setup_fields()

    def _setup_fields(self) -> None:
        """Set up the panel fields."""
        # Audio padding
        self.audio_padding_spinbox = QDoubleSpinBox()
        self.audio_padding_spinbox.setRange(0.0, 5.0)
        self.audio_padding_spinbox.setSingleStep(0.1)
        self.audio_padding_spinbox.setSuffix(" seconds")
        self.audio_padding_spinbox.setToolTip("Extra time to include before and after audio clips")
        self.add_field(
            "Audio Padding",
            self.audio_padding_spinbox,
            helper="Extra time to include before and after the subtitle timing",
        )

        # Screenshot offset
        self.screenshot_offset_spinbox = QDoubleSpinBox()
        self.screenshot_offset_spinbox.setRange(0.0, 10.0)
        self.screenshot_offset_spinbox.setSingleStep(0.1)
        self.screenshot_offset_spinbox.setSuffix(" seconds")
        self.screenshot_offset_spinbox.setToolTip("Time offset for screenshot capture")
        self.add_field(
            "Screenshot Offset",
            self.screenshot_offset_spinbox,
            helper="Time offset from subtitle start for screenshot capture",
        )

        # Max workers
        self.max_workers_spinbox = QSpinBox()
        self.max_workers_spinbox.setRange(1, 20)
        self.max_workers_spinbox.setToolTip("Number of parallel workers for processing")
        self.add_field(
            "Max Parallel Workers",
            self.max_workers_spinbox,
            helper="Higher values = faster processing but more CPU/memory usage",
        )

        # Animated screenshot toggle
        self.animated_checkbox = QCheckBox("Enable animated screenshots")
        self.animated_checkbox.setToolTip(
            "Capture a short video clip instead of a static frame. "
            "Larger files, slower encode; not all Anki clients render animated AVIF/WebP."
        )
        self.add_field("Animated Screenshots", self.animated_checkbox)

        # Format
        self.animated_format_combo = QComboBox()
        self.animated_format_combo.addItems(["avif", "webp"])
        self.add_field(
            "Animated Format",
            self.animated_format_combo,
            helper="AVIF: smaller files; WebP: broader Anki client support",
        )

        # Match audio duration toggle
        self.animated_match_audio_checkbox = QCheckBox("Match audio duration")
        self.animated_match_audio_checkbox.setToolTip(
            "When enabled, the animated clip spans the same time range as the audio "
            "clip (subtitle range plus audio padding on both sides). Overrides Clip Duration."
        )
        self.add_field("Match Audio Duration", self.animated_match_audio_checkbox)

        # Clip duration
        self.animated_duration_spinbox = QDoubleSpinBox()
        self.animated_duration_spinbox.setRange(0.5, 10.0)
        self.animated_duration_spinbox.setSingleStep(0.5)
        self.animated_duration_spinbox.setSuffix(" seconds")
        self.animated_duration_spinbox.setToolTip(
            "Maximum clip length, capped by the subtitle duration. "
            "Ignored when 'Match Audio Duration' is enabled."
        )
        self.add_field("Clip Duration", self.animated_duration_spinbox)

        # FPS
        self.animated_fps_spinbox = QSpinBox()
        self.animated_fps_spinbox.setRange(5, 30)
        self.animated_fps_spinbox.setToolTip("Frames per second for animated clips")
        self.add_field("FPS", self.animated_fps_spinbox)

        # Height
        self.animated_height_spinbox = QSpinBox()
        self.animated_height_spinbox.setRange(240, 1080)
        self.animated_height_spinbox.setSingleStep(120)
        self.animated_height_spinbox.setSuffix(" px")
        self.add_field(
            "Height",
            self.animated_height_spinbox,
            helper="Output height; aspect ratio preserved",
        )

        # Quality
        self.animated_quality_spinbox = QSpinBox()
        self.animated_quality_spinbox.setRange(0, 100)
        self.animated_quality_spinbox.setToolTip("0 = smallest file, 100 = best quality")
        self.add_field(
            "Quality",
            self.animated_quality_spinbox,
            helper="Mind your AnkiWeb media quota at high quality settings",
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
            self.animated_fps_spinbox,
            self.animated_height_spinbox,
            self.animated_quality_spinbox,
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
