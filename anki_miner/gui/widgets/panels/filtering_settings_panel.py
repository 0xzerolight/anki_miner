"""Word filtering settings panel."""

import logging
from pathlib import Path

from PyQt6.QtWidgets import QCheckBox, QSpinBox

from anki_miner.gui.widgets.base import FormPanel
from anki_miner.gui.widgets.enhanced import FileSelector

logger = logging.getLogger(__name__)


class FilteringSettingsPanel(FormPanel):
    """Panel for word filtering settings.

    Provides:
    - Word frequency filtering options
    """

    def __init__(self, parent=None):
        """Initialize the filtering settings panel."""
        super().__init__("Word Filtering", parent=parent)
        self._setup_fields()
        self._connect_validation()

    def _setup_fields(self) -> None:
        """Set up the panel fields."""
        # Word Frequency section
        self.add_section("Word Frequency")

        # Frequency file path
        self.frequency_selector = FileSelector(
            label="", file_mode=True, placeholder="Select frequency list CSV..."
        )
        self.frequency_selector.setToolTip("Path to word frequency list CSV")
        self.add_field(
            "Frequency List File",
            self.frequency_selector,
            helper="Path to a Japanese word frequency list (CSV format: word, rank)",
        )

        # Use frequency data checkbox
        self.use_frequency_checkbox = QCheckBox("Enable Frequency Data")
        self.use_frequency_checkbox.setToolTip("Attach word frequency ranks to cards")
        self.add_field(
            "",
            self.use_frequency_checkbox,
            helper="Enable to display word frequency rank on cards",
        )

        # Max frequency rank
        self.max_frequency_spinbox = QSpinBox()
        self.max_frequency_spinbox.setRange(0, 100000)
        self.max_frequency_spinbox.setSpecialValueText("No limit")
        self.max_frequency_spinbox.setToolTip(
            "Only mine words within the top N most frequent (0 = no limit)"
        )
        self.add_field(
            "Max Frequency Rank",
            self.max_frequency_spinbox,
            helper="Set to 0 for no limit, or e.g. 10000 to only mine top 10,000 words",
        )

        # Known Words Database section
        self.add_section("Known Words Database")

        self.use_known_words_db_checkbox = QCheckBox("Use Local Known Words Database")
        self.use_known_words_db_checkbox.setToolTip("Cache known words locally for faster startup")
        self.add_field(
            "",
            self.use_known_words_db_checkbox,
            helper="Caches known words in a local SQLite database to avoid querying Anki on every run",
        )

        # Word Lists section
        self.add_section("Word Lists")

        self.blacklist_selector = FileSelector(
            label="", file_mode=True, placeholder="Select blacklist file..."
        )
        self.add_field(
            "Blacklist File",
            self.blacklist_selector,
            helper="Text file with one word per line to always skip",
        )

        self.use_blacklist_checkbox = QCheckBox("Enable Blacklist")
        self.add_field(
            "",
            self.use_blacklist_checkbox,
            helper="Skip words found in the blacklist file",
        )

        self.whitelist_selector = FileSelector(
            label="", file_mode=True, placeholder="Select whitelist file..."
        )
        self.add_field(
            "Whitelist File",
            self.whitelist_selector,
            helper="Text file with one word per line to always include",
        )

        self.use_whitelist_checkbox = QCheckBox("Enable Whitelist")
        self.add_field(
            "",
            self.use_whitelist_checkbox,
            helper="Always include words found in the whitelist file",
        )

        # Deduplication section
        self.add_section("Deduplication")

        self.deduplicate_sentences_checkbox = QCheckBox("Deduplicate by Sentence")
        self.deduplicate_sentences_checkbox.setToolTip(
            "Skip words that share an identical sentence with an already-selected word"
        )
        self.add_field(
            "",
            self.deduplicate_sentences_checkbox,
            helper="Skip words that share an identical sentence with an already-selected word",
        )

        self.add_stretch()

    def _connect_validation(self) -> None:
        """Connect file selector signals to validation handlers."""
        self.frequency_selector.path_validated.connect(self._validate_frequency_file)

    def _validate_frequency_file(self, is_valid: bool, path_str: str) -> None:
        """Validate frequency file and show entry count."""
        if not is_valid or not path_str:
            return

        try:
            from anki_miner.services.frequency_service import FrequencyService

            service = FrequencyService(Path(path_str))
            service.load()
            count = service.entry_count
            self.frequency_selector.status_label.setText(
                f"{Path(path_str).name} ({count:,} entries)"
            )
        except Exception as e:
            self.frequency_selector.status_label.setText(f"Could not parse file: {e}")
