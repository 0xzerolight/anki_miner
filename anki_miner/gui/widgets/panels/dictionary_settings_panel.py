"""Dictionary settings panel."""

from PyQt6.QtWidgets import QCheckBox

from anki_miner.gui.widgets.base import FormPanel
from anki_miner.gui.widgets.enhanced import FileSelector


class DictionarySettingsPanel(FormPanel):
    """Panel for dictionary configuration settings.

    Provides:
    - JMdict file path selection
    - Offline dictionary toggle
    - Pitch accent file path selection
    - Pitch accent toggle
    """

    def __init__(self, parent=None):
        """Initialize the dictionary settings panel."""
        super().__init__("Dictionary Settings", icon="word", parent=parent)
        self._setup_fields()

    def _setup_fields(self) -> None:
        """Set up the panel fields."""
        # JMdict path
        self.jmdict_selector = FileSelector(
            label="", file_mode=True, placeholder="Select JMdict file..."
        )
        self.jmdict_selector.setToolTip("Path to JMdict dictionary file")
        self.add_field(
            "JMdict Path",
            self.jmdict_selector,
            helper="Path to JMdict XML file for offline dictionary lookups",
        )

        # Use offline dictionary checkbox
        self.use_offline_checkbox = QCheckBox("Use Offline Dictionary")
        self.use_offline_checkbox.setToolTip("Use local JMdict instead of online API")
        self.add_field(
            "",
            self.use_offline_checkbox,
            helper="Enable to use local JMdict file instead of online dictionary API",
        )

        # Pitch Accent section
        self.add_section("Pitch Accent")

        # Pitch accent file path
        self.pitch_accent_selector = FileSelector(
            label="", file_mode=True, placeholder="Select pitch accent CSV file..."
        )
        self.pitch_accent_selector.setToolTip("Path to Kanjium pitch accent CSV file")
        self.add_field(
            "Pitch Accent File",
            self.pitch_accent_selector,
            helper="Path to Kanjium pitch accent CSV for pitch accent annotations",
        )

        # Use pitch accent checkbox
        self.use_pitch_accent_checkbox = QCheckBox("Enable Pitch Accent")
        self.use_pitch_accent_checkbox.setToolTip("Add pitch accent data to Anki cards")
        self.add_field(
            "",
            self.use_pitch_accent_checkbox,
            helper="Enable to look up and add pitch accent patterns to cards",
        )

        self.add_stretch()
