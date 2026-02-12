"""Media extraction settings panel."""

from PyQt6.QtWidgets import QDoubleSpinBox, QSpinBox

from anki_miner.gui.widgets.base import FormPanel


class MediaSettingsPanel(FormPanel):
    """Panel for media extraction settings.

    Provides:
    - Audio padding configuration
    - Screenshot offset configuration
    - Max parallel workers configuration
    """

    def __init__(self, parent=None):
        """Initialize the media settings panel."""
        super().__init__("Media Extraction Settings", icon="video", parent=parent)
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

        self.add_stretch()
