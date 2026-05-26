"""Word filtering settings panel."""

import logging
from pathlib import Path

from PyQt6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QWidget,
)

from anki_miner.gui.widgets.base import FormPanel
from anki_miner.gui.widgets.enhanced import FileSelector

logger = logging.getLogger(__name__)

# Built-in regex presets for common subtitle noise. Buttons append these to the
# user's pattern with `|` so multiple presets can be stacked. Patterns target
# both half-width and full-width punctuation common in JP subtitle files.
SUBTITLE_REGEX_PRESETS: tuple[tuple[str, str], ...] = (
    ("Parens (Tanaka)", r"\([^)]*\)|（[^）]*）"),
    ("Brackets [SFX]", r"\[[^\]]*\]|［[^］]*］"),
    ("Music ♪♬", r"[♪♬♫#～〜]+"),
    ("Speaker: prefix", r"^[^「『:：]+[:：]\s*"),
)


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

        # Frequency file path. Accepts CSV/TSV directly, or a Yomitan-format
        # frequency zip — the latter is converted to CSV on Save (see
        # SettingsTab._on_save_clicked).
        self.frequency_selector = FileSelector(
            label="",
            file_mode=True,
            file_filter="Frequency list (*.csv *.tsv *.txt *.zip);;All Files (*)",
            placeholder="Select frequency list CSV/TSV or Yomitan zip...",
        )
        self.frequency_selector.setToolTip("CSV/TSV file or Yomitan-format frequency zip")
        self.add_field(
            "Frequency List File",
            self.frequency_selector,
            helper=(
                "CSV/TSV with columns (word, rank), or a Yomitan-format "
                "frequency zip (e.g. JPDB, BCCWJ). Yomitan zips are imported "
                "into ~/.anki_miner/frequency.csv on Save."
            ),
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
        self.max_frequency_spinbox.setToolTip("Only mine words within the top N most frequent (0 = no limit)")
        self.add_field(
            "Max Frequency Rank",
            self.max_frequency_spinbox,
            helper="Set to 0 for no limit, or e.g. 10000 to only mine top 10,000 words. Words missing from the frequency list are excluded.",
        )

        # Known Words Database section
        self.add_section("Known Words Database")

        self.use_known_words_db_checkbox = QCheckBox("Use Local Known Words Database")
        self.add_field(
            "",
            self.use_known_words_db_checkbox,
            helper="Caches known words locally to skip the Anki query on every run.",
        )

        # Word Lists section
        self.add_section("Word Lists")

        self.blacklist_selector = FileSelector(label="", file_mode=True, placeholder="Select blacklist file...")
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

        self.whitelist_selector = FileSelector(label="", file_mode=True, placeholder="Select whitelist file...")
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

        # Subtitle Text Filtering section (Issue #8)
        self.add_section("Subtitle Text Filtering")

        self.subtitle_regex_edit = QLineEdit()
        self.subtitle_regex_edit.setPlaceholderText(r"e.g. \([^)]*\)|\[[^\]]*\]")
        self.subtitle_regex_edit.setToolTip(
            "Python regex applied to each subtitle line before tokenization. "
            "Combine alternatives with |. Test patterns at https://regex101.com."
        )
        self.add_field(
            "Regex Filter",
            self.subtitle_regex_edit,
            helper="Patterns matched in subtitle text are removed (or replaced) before mining. "
            "Useful for stripping speaker names like (Tanaka) or sound descriptions like [door].",
        )

        self.subtitle_replacement_edit = QLineEdit()
        self.subtitle_replacement_edit.setPlaceholderText("(empty = delete match)")
        self.subtitle_replacement_edit.setToolTip("Text inserted in place of each match. Empty deletes the match.")
        self.add_field(
            "Replacement",
            self.subtitle_replacement_edit,
            helper="Use Python backreferences (\\1 \\2) for capture groups. "
            "Note: NOT $1 $2 syntax like asbplayer; translate when copying patterns.",
        )

        self.use_subtitle_regex_checkbox = QCheckBox("Enable Subtitle Regex Filter")
        self.add_field(
            "",
            self.use_subtitle_regex_checkbox,
            helper="Apply the filter to all parsed subtitle lines (mining and preview).",
        )

        # Preset buttons row: each click appends its pattern to the regex field
        # joined with `|`. Lets a GUI-only user discover useful patterns without
        # learning regex syntax up front.
        preset_container = QWidget()
        preset_layout = QHBoxLayout()
        preset_layout.setContentsMargins(0, 0, 0, 0)
        for label, pattern in SUBTITLE_REGEX_PRESETS:
            btn = QPushButton(label)
            btn.setToolTip(pattern)
            btn.clicked.connect(lambda _checked=False, p=pattern: self._append_preset(p))
            preset_layout.addWidget(btn)
        preset_layout.addStretch()
        preset_container.setLayout(preset_layout)
        self.add_field(
            "Presets",
            preset_container,
            helper="Click to append a built-in pattern to the regex field above.",
        )

        # Deduplication section
        self.add_section("Deduplication")

        self.deduplicate_sentences_checkbox = QCheckBox("Deduplicate by Sentence")
        self.add_field(
            "",
            self.deduplicate_sentences_checkbox,
            helper="Skips duplicate example sentences.",
        )

        # i+1 Sentence Filter section
        self.add_section("i+1 Sentence Filter")

        self.use_i_plus_one_checkbox = QCheckBox("Only Mine i+1 Sentences")
        self.use_i_plus_one_checkbox.setToolTip(
            "Only create cards for words that appear in a sentence with exactly ONE "
            "unknown word (the i+1 / immersion learning concept). Drops words whose "
            "only examples contain multiple unknowns, so expect significantly fewer "
            "cards per episode."
        )
        self.add_field(
            "",
            self.use_i_plus_one_checkbox,
            helper="Keep only words with at least one example sentence where they are "
            "the sole unknown. Trades card volume for sentence comprehensibility. "
            "Overrides sentence deduplication when enabled.",
        )

        # Sentence Length section (Issue #33)
        self.add_section("Sentence Length")

        self.use_sentence_length_checkbox = QCheckBox("Enable Sentence Length Filter")
        self.use_sentence_length_checkbox.setToolTip(
            "Drop words whose example sentence exceeds the audio-duration "
            "or character caps below. Either cap set to 0 means no limit "
            "for that dimension."
        )
        self.add_field(
            "",
            self.use_sentence_length_checkbox,
            helper="Skip cards with long example sentences to reduce deck " "size and speed up reviews.",
        )

        self.max_sentence_duration_spinbox = QDoubleSpinBox()
        self.max_sentence_duration_spinbox.setRange(0.0, 600.0)
        self.max_sentence_duration_spinbox.setDecimals(1)
        self.max_sentence_duration_spinbox.setSingleStep(0.5)
        self.max_sentence_duration_spinbox.setSuffix(" s")
        self.max_sentence_duration_spinbox.setSpecialValueText("No limit")
        self.max_sentence_duration_spinbox.setToolTip(
            "Maximum audio length of the example sentence in seconds " "(0 = no limit)"
        )
        self.add_field(
            "Max Sentence Duration",
            self.max_sentence_duration_spinbox,
            helper="Drops cards whose subtitle line is longer than this. " "Set to 0 for no limit.",
        )

        self.max_sentence_chars_spinbox = QSpinBox()
        self.max_sentence_chars_spinbox.setRange(0, 1000)
        self.max_sentence_chars_spinbox.setSpecialValueText("No limit")
        self.add_field(
            "Max Sentence Characters",
            self.max_sentence_chars_spinbox,
            helper="Drops cards whose sentence text exceeds this many characters. Set to 0 for no limit.",
        )

        # Card Formatting section (Issue #20)
        self.add_section("Card Formatting")

        self.bold_target_in_sentence_checkbox = QCheckBox("Bold target word in sentence")
        # QToolTip has no PlainText format and auto-detects HTML when the
        # string contains tag-like substrings. Escape the angle brackets so
        # the literal "<b>...</b>" markup is visible. Issue #20.
        self.bold_target_in_sentence_checkbox.setToolTip(
            "Wrap the mined word in &lt;b&gt;...&lt;/b&gt; inside the Sentence and "
            "SentenceFurigana fields. Match is the exact MeCab span of the "
            "mined morpheme, so duplicated surfaces in a sentence only bold "
            "the actually-mined occurrence."
        )
        self.add_field(
            "",
            self.bold_target_in_sentence_checkbox,
            helper="Wraps mined word in <b>...</b> in Sentence and SentenceFurigana fields.",
        )

        self.add_stretch()

    def _append_preset(self, pattern: str) -> None:
        """Append a preset regex pattern to the filter field with `|` join."""
        current = self.subtitle_regex_edit.text().strip()
        if not current:
            self.subtitle_regex_edit.setText(pattern)
        elif pattern in current:
            # Avoid duplicate alternations from double-clicking a preset.
            return
        else:
            self.subtitle_regex_edit.setText(f"{current}|{pattern}")

    def _connect_validation(self) -> None:
        """Connect file selector signals to validation handlers."""
        self.frequency_selector.path_validated.connect(self._validate_frequency_file)

    def _validate_frequency_file(self, is_valid: bool, path_str: str) -> None:
        """Validate frequency file and show entry count.

        For ``.zip`` paths we don't parse — the actual Yomitan import runs on
        Save (where progress + error dialogs are wired). Showing a "will import"
        hint here keeps the slow extract off the validation hot path.
        """
        if not is_valid or not path_str:
            return

        if path_str.lower().endswith(".zip"):
            self.frequency_selector.status_label.setText(f"{Path(path_str).name} (Yomitan zip — will import on Save)")
            return

        try:
            from anki_miner.services.frequency_service import FrequencyService

            service = FrequencyService(Path(path_str))
            service.load()
            count = service.entry_count
            self.frequency_selector.status_label.setText(f"{Path(path_str).name} ({count:,} entries)")
        except Exception as e:
            self.frequency_selector.status_label.setText(f"Could not parse file: {e}")
