"""Word filtering settings panel."""

from PyQt6.QtWidgets import QCheckBox, QSpinBox

from anki_miner.gui.widgets.base import FormPanel
from anki_miner.gui.widgets.enhanced import FileSelector


class FilteringSettingsPanel(FormPanel):
    """Panel for word filtering settings.

    Provides:
    - Minimum word length configuration
    - Word frequency filtering options
    """

    def __init__(self, parent=None):
        """Initialize the filtering settings panel."""
        super().__init__("Word Filtering", icon="filter", parent=parent)
        self._setup_fields()

    def _setup_fields(self) -> None:
        """Set up the panel fields."""
        # Minimum word length
        self.min_length_spinbox = QSpinBox()
        self.min_length_spinbox.setRange(1, 10)
        self.min_length_spinbox.setToolTip("Minimum character length for words to be processed")
        self.add_field(
            "Minimum Word Length",
            self.min_length_spinbox,
            helper="Words shorter than this will be ignored during processing",
        )

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
