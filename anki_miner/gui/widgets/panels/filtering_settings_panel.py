"""Word filtering settings panel."""

import logging
from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSpinBox,
    QWidget,
)

from anki_miner.gui.widgets.base import FormPanel
from anki_miner.gui.widgets.enhanced import FileSelector
from anki_miner.services.wordset_service import load_wordset_catalog

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
    - Known-words database toggle, deck exclusions, and cache rebuild (Issue #38)

    Signals:
        fetch_decks_requested: Emitted when the deck list must be fetched from
            AnkiConnect to populate the "Add Deck…" picker.
        rebuild_known_words_requested: Emitted when the user asks to clear the
            local known-words cache.
        manage_known_words_requested: Emitted when the user opens the Manage
            Known Words dialog (Issue #42).
    """

    fetch_decks_requested = pyqtSignal()
    rebuild_known_words_requested = pyqtSignal()
    manage_known_words_requested = pyqtSignal()

    def __init__(self, parent=None):
        """Initialize the filtering settings panel."""
        # Deck names fetched from AnkiConnect, cached so re-opening the picker
        # doesn't trigger another round-trip. Empty until the first fetch.
        self._available_decks: list[str] = []
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

        # Rebuild button: clears the local cache so deck exclusions take effect.
        # The cache is additive (never removes), so a deck synced before being
        # excluded would otherwise stay cached forever (Issue #38).
        rebuild_row = QHBoxLayout()
        self.rebuild_known_words_button = QPushButton("Rebuild Known Words DB")
        self.rebuild_known_words_button.setToolTip(
            "Clear the local known-words cache so it re-syncs from Anki on the "
            "next run. Needed for deck exclusions below to take effect when the "
            "local cache is enabled."
        )
        self.rebuild_known_words_button.clicked.connect(self.rebuild_known_words_requested.emit)
        rebuild_row.addWidget(self.rebuild_known_words_button)

        # Manage the user-curated known/ignore list (Issue #42): view, remove,
        # export, reset words added from the Word Curator.
        self.manage_known_words_button = QPushButton("Manage Known Words…")
        self.manage_known_words_button.setToolTip(
            "View, remove, export, or reset the words you added to your local "
            "known words list from the Word Curator."
        )
        self.manage_known_words_button.clicked.connect(self.manage_known_words_requested.emit)
        rebuild_row.addWidget(self.manage_known_words_button)
        rebuild_row.addStretch()
        self.add_layout(rebuild_row)

        # Excluded decks (Issue #38)
        self.add_section("Excluded Decks")

        excluded_helper = QLabel(
            "Words in these decks (and their subdecks) are NOT treated as already "
            "known, so they stay mineable. Useful for kanji-shape decks like "
            "Remembering The Kanji that don't teach vocabulary."
        )
        excluded_helper.setObjectName("helper-text")
        excluded_helper.setWordWrap(True)
        self.add_widget(excluded_helper)

        self.excluded_decks_list = QListWidget()
        self.excluded_decks_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.excluded_decks_list.setMaximumHeight(140)
        self.add_widget(self.excluded_decks_list)

        excluded_buttons = QHBoxLayout()
        self.add_deck_button = QPushButton("Add Deck…")
        self.add_deck_button.clicked.connect(self._on_add_deck_clicked)
        self.remove_deck_button = QPushButton("Remove")
        self.remove_deck_button.clicked.connect(self._on_remove_deck_clicked)
        excluded_buttons.addWidget(self.add_deck_button)
        excluded_buttons.addWidget(self.remove_deck_button)
        excluded_buttons.addStretch()
        self.add_layout(excluded_buttons)

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

        # Name Wordsets section (Issue #59). Bundled proper-noun lists derived
        # from JMnedict; checking one excludes those names from mining. Catches
        # names unidic-lite mistags as common nouns (the POS filter only drops
        # proper nouns the parser actually recognizes as 固有名詞).
        self.add_section("Name Wordsets")

        wordsets_helper = QLabel(
            "Exclude bundled lists of Japanese proper nouns (people and place "
            "names) from mining. Useful for anime that drop lots of character "
            "and place names. A name you actually want is rescued by the "
            "whitelist above."
        )
        wordsets_helper.setObjectName("helper-text")
        wordsets_helper.setWordWrap(True)
        self.add_widget(wordsets_helper)

        self.wordset_checkboxes: dict[str, QCheckBox] = {}
        for info in load_wordset_catalog():
            cb = QCheckBox(f"{info.label} ({info.count:,})")
            cb.setToolTip(f"Exclude the bundled '{info.label}' wordset ({info.count:,} entries) from mining.")
            self.wordset_checkboxes[info.id] = cb
            self.add_field("", cb, helper="")

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

    # --- Excluded decks (Issue #38) ---

    def _on_add_deck_clicked(self) -> None:
        """Open the deck picker, fetching the deck list first if needed.

        On the first click ``_available_decks`` is empty, so we request a fetch
        and defer opening the picker until :meth:`set_available_decks` is called
        with the result. Subsequent clicks reuse the cached list.
        """
        if not self._available_decks:
            self.fetch_decks_requested.emit()
            return
        self._open_deck_picker()

    def set_available_decks(self, decks: list[str]) -> None:
        """Receive the fetched deck list and open the picker.

        Called from the settings tab once the fetch worker finishes.
        """
        self._available_decks = list(decks)
        self._open_deck_picker()

    def _open_deck_picker(self) -> None:
        """Prompt the user to pick a deck not already excluded."""
        already = set(self._listed_decks())
        choices = [d for d in self._available_decks if d not in already]
        if not choices:
            return
        deck, ok = QInputDialog.getItem(
            self,
            "Exclude Deck",
            "Deck to exclude from known-words detection:",
            choices,
            0,
            False,
        )
        if ok and deck:
            self.excluded_decks_list.addItem(deck)

    def _on_remove_deck_clicked(self) -> None:
        """Remove the currently selected excluded deck."""
        row = self.excluded_decks_list.currentRow()
        if row >= 0:
            self.excluded_decks_list.takeItem(row)

    def _listed_decks(self) -> list[str]:
        """Return the deck names currently in the list widget."""
        items = (self.excluded_decks_list.item(i) for i in range(self.excluded_decks_list.count()))
        return [item.text() for item in items if item is not None]

    def get_excluded_decks(self) -> tuple[str, ...]:
        """Return the excluded deck names from the list widget."""
        return tuple(self._listed_decks())

    def set_excluded_decks(self, decks: tuple[str, ...]) -> None:
        """Populate the list widget from config."""
        self.excluded_decks_list.clear()
        for deck in decks:
            self.excluded_decks_list.addItem(deck)

    # --- Name Wordsets (Issue #59) ---

    def get_excluded_wordsets(self) -> tuple[str, ...]:
        """Return the IDs of the checked name wordsets, in catalog order."""
        return tuple(set_id for set_id, cb in self.wordset_checkboxes.items() if cb.isChecked())

    def set_excluded_wordsets(self, ids: tuple[str, ...]) -> None:
        """Check the wordset boxes whose IDs are in ``ids``."""
        wanted = set(ids)
        for set_id, cb in self.wordset_checkboxes.items():
            cb.setChecked(set_id in wanted)

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
