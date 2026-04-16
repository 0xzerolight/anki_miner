"""Dictionary settings panel."""

import logging
from pathlib import Path

from PyQt6.QtWidgets import QCheckBox

from anki_miner.gui.widgets.base import FormPanel
from anki_miner.gui.widgets.enhanced import FileSelector

logger = logging.getLogger(__name__)


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
        super().__init__("Dictionary Settings", parent=parent)
        self._setup_fields()
        self._connect_validation()

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
            label="", file_mode=True, placeholder="Select pitch accent file (CSV/TSV)..."
        )
        self.pitch_accent_selector.setToolTip("Path to pitch accent data file (CSV or TSV)")
        self.add_field(
            "Pitch Accent File",
            self.pitch_accent_selector,
            helper="CSV/TSV file with columns: reading, kanji, pattern (e.g. Kanjium format)",
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

    def _connect_validation(self) -> None:
        """Connect file selector signals to validation handlers."""
        self.pitch_accent_selector.path_validated.connect(self._validate_pitch_accent_file)

    def _validate_pitch_accent_file(self, is_valid: bool, path_str: str) -> None:
        """Validate pitch accent file and show entry count."""
        if not is_valid or not path_str:
            return

        try:
            from anki_miner.services.pitch_accent_service import PitchAccentService

            service = PitchAccentService(Path(path_str))
            service.load()
            count = service.entry_count
            if count > 0:
                self.pitch_accent_selector.status_label.setText(
                    f"{Path(path_str).name} ({count:,} entries)"
                )
            else:
                self.pitch_accent_selector.status_label.setText(
                    f"{Path(path_str).name} (0 entries - check file format)"
                )
        except Exception as e:
            self.pitch_accent_selector.status_label.setText(f"Could not parse file: {e}")
