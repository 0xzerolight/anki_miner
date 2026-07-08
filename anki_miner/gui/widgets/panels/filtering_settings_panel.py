"""Word filtering settings panel."""

import logging
from dataclasses import replace
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
from anki_miner.utils.i18n import tr_format

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

    def _setup_fields(self) -> None:
        """Set up the panel fields."""
        # Word Frequency section. The frequency *file selector + enable toggle*
        # now live in the Dictionaries tab (users think of the frequency list as
        # a dictionary); only the max-rank threshold — a filter — stays here.
        self.add_section(self.tr("Word Frequency"))

        # Max frequency rank
        self.max_frequency_spinbox = QSpinBox()
        self.max_frequency_spinbox.setRange(0, 100000)
        self.max_frequency_spinbox.setSpecialValueText(self.tr("No limit"))
        self.add_field(
            self.tr("Max Frequency Rank"),
            self.max_frequency_spinbox,
            helper=self.tr("Words missing from the frequency list are excluded"),
        )
        # Shown by load_from_config only when a cutoff is set but no frequency
        # source is enabled. In that state the pipeline gates the cutoff off (it
        # would otherwise drop every word and create zero cards), so warn here
        # instead of letting the spinbox look active.
        self.max_frequency_warning = QLabel(
            self.tr(
                "No frequency source is loaded — this cutoff is ignored. "
                "Add a frequency source in the Dictionaries tab."
            )
        )
        self.max_frequency_warning.setObjectName("helper-text")
        self.max_frequency_warning.setWordWrap(True)
        self.max_frequency_warning.setVisible(False)
        self.add_widget(self.max_frequency_warning)

        # Known Words Database section
        self.add_section(self.tr("Known Words Database"))

        self.use_known_words_db_checkbox = QCheckBox(self.tr("Use Local Known Words Database"))
        self.add_field("", self.use_known_words_db_checkbox)

        # Rebuild button: clears the local cache so deck exclusions take effect.
        # The cache is additive (never removes), so a deck synced before being
        # excluded would otherwise stay cached forever (Issue #38).
        rebuild_row = QHBoxLayout()
        self.rebuild_known_words_button = QPushButton(self.tr("Rebuild Known Words DB"))
        self.rebuild_known_words_button.setToolTip(
            self.tr(
                "Clear the local known-words cache so it re-syncs from Anki on the "
                "next run. Needed for deck exclusions below to take effect when the "
                "local cache is enabled."
            )
        )
        self.rebuild_known_words_button.clicked.connect(self.rebuild_known_words_requested.emit)
        rebuild_row.addWidget(self.rebuild_known_words_button)

        # Manage the user-curated known/ignore list (Issue #42): view, remove,
        # export, reset words added from the Word Curator.
        self.manage_known_words_button = QPushButton(self.tr("Manage Known Words…"))
        self.manage_known_words_button.setToolTip(
            self.tr(
                "View, remove, export, or reset the words you added to your local "
                "known words list from the Word Curator."
            )
        )
        self.manage_known_words_button.clicked.connect(self.manage_known_words_requested.emit)
        rebuild_row.addWidget(self.manage_known_words_button)
        rebuild_row.addStretch()
        self.add_layout(rebuild_row)

        # Excluded decks (Issue #38)
        self.add_section(self.tr("Excluded Decks"))

        excluded_helper = QLabel(
            self.tr("Words in these decks (and their subdecks) stay mineable — not treated " "as already known.")
        )
        excluded_helper.setObjectName("helper-text")
        excluded_helper.setWordWrap(True)
        self.add_widget(excluded_helper)

        self.excluded_decks_list = QListWidget()
        self.excluded_decks_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.excluded_decks_list.setMaximumHeight(140)
        self.add_widget(self.excluded_decks_list)

        excluded_buttons = QHBoxLayout()
        self.add_deck_button = QPushButton(self.tr("Add Deck…"))
        self.add_deck_button.clicked.connect(self._on_add_deck_clicked)
        self.remove_deck_button = QPushButton(self.tr("Remove"))
        self.remove_deck_button.clicked.connect(self._on_remove_deck_clicked)
        excluded_buttons.addWidget(self.add_deck_button)
        excluded_buttons.addWidget(self.remove_deck_button)
        excluded_buttons.addStretch()
        self.add_layout(excluded_buttons)

        # Word Lists section
        self.add_section(self.tr("Word Lists"))

        self.blacklist_selector = FileSelector(
            label="", file_mode=True, placeholder=self.tr("Select blacklist file...")
        )
        self.add_field(
            self.tr("Blacklist File"),
            self.blacklist_selector,
            helper=self.tr("Text file with one word per line to always skip"),
        )

        self.use_blacklist_checkbox = QCheckBox(self.tr("Enable Blacklist"))
        self.add_field("", self.use_blacklist_checkbox)

        self.whitelist_selector = FileSelector(
            label="", file_mode=True, placeholder=self.tr("Select whitelist file...")
        )
        self.add_field(
            self.tr("Whitelist File"),
            self.whitelist_selector,
            helper=self.tr(
                "Text file with one word per line to force-include, bypassing frequency, "
                "script, length and other filters. A word must still have a dictionary entry "
                "and not already be in Anki or your known-words list."
            ),
        )

        self.use_whitelist_checkbox = QCheckBox(self.tr("Enable Whitelist"))
        self.add_field("", self.use_whitelist_checkbox)

        # Name Wordsets section (Issue #59). Bundled proper-noun lists derived
        # from JMnedict; checking one excludes those names from mining. Catches
        # names unidic-lite mistags as common nouns (the POS filter only drops
        # proper nouns the parser actually recognizes as 固有名詞).
        self.add_section(self.tr("Name Wordsets"))

        wordsets_helper = QLabel(
            self.tr(
                "Exclude bundled lists of Japanese proper nouns (people and place "
                "names) from mining. Useful for shows that drop lots of character "
                "and place names. A name you actually want is rescued by the "
                "whitelist above."
            )
        )
        wordsets_helper.setObjectName("helper-text")
        wordsets_helper.setWordWrap(True)
        self.add_widget(wordsets_helper)

        self.wordset_checkboxes: dict[str, QCheckBox] = {}
        for info in load_wordset_catalog():
            cb = QCheckBox(tr_format(self.tr("%1 (%2)"), info.label, f"{info.count:,}"))
            cb.setToolTip(
                tr_format(
                    self.tr("Exclude the bundled '%1' wordset (%2 entries) from mining."), info.label, f"{info.count:,}"
                )
            )
            self.wordset_checkboxes[info.id] = cb
            self.add_field("", cb)

        # Subtitle Text Filtering section (Issue #8)
        self.add_section(self.tr("Subtitle Text Filtering"))

        self.subtitle_regex_edit = QLineEdit()
        self.subtitle_regex_edit.setPlaceholderText(r"e.g. \([^)]*\)|\[[^\]]*\]")
        self.add_field(
            self.tr("Regex Filter"),
            self.subtitle_regex_edit,
            helper=self.tr(
                "Python regex matched in subtitle text and removed (or replaced) before mining. "
                "Useful for stripping speaker names like (Tanaka) or sound descriptions like [door]. "
                "Combine alternatives with |. Test patterns at https://regex101.com."
            ),
        )

        self.subtitle_replacement_edit = QLineEdit()
        self.subtitle_replacement_edit.setPlaceholderText(self.tr("(empty = delete match)"))
        self.add_field(
            self.tr("Replacement"),
            self.subtitle_replacement_edit,
            helper=self.tr(
                "Inserted in place of each match (empty deletes it). Use Python "
                "backreferences \\1 \\2, not asbplayer's $1 $2."
            ),
        )

        self.use_subtitle_regex_checkbox = QCheckBox(self.tr("Enable Subtitle Regex Filter"))
        self.add_field("", self.use_subtitle_regex_checkbox)

        # Preset buttons row: each click appends its pattern to the regex field
        # joined with `|`. Lets a GUI-only user discover useful patterns without
        # learning regex syntax up front.
        preset_container = QWidget()
        preset_layout = QHBoxLayout()
        preset_layout.setContentsMargins(0, 0, 0, 0)
        _preset_labels = [
            self.tr("Parens (Tanaka)"),
            self.tr("Brackets [SFX]"),
            self.tr("Music ♪♬"),
            self.tr("Speaker: prefix"),
        ]
        for (_, pattern), translated_label in zip(SUBTITLE_REGEX_PRESETS, _preset_labels, strict=True):
            btn = QPushButton(translated_label)
            btn.setToolTip(pattern)
            btn.clicked.connect(lambda _checked=False, p=pattern: self._append_preset(p))
            preset_layout.addWidget(btn)
        preset_layout.addStretch()
        preset_container.setLayout(preset_layout)
        self.add_field(
            self.tr("Presets"),
            preset_container,
            helper=self.tr("Click to append a built-in pattern to the regex field above."),
        )

        # Deduplication section
        self.add_section(self.tr("Deduplication"))

        self.deduplicate_sentences_checkbox = QCheckBox(self.tr("Deduplicate by Sentence"))
        self.add_field(
            "",
            self.deduplicate_sentences_checkbox,
            helper=self.tr("Skips duplicate example sentences."),
        )

        # Script Type section (Issue #57)
        self.add_section(self.tr("Script Type"))

        self.exclude_hiragana_only_checkbox = QCheckBox(self.tr("Exclude Hiragana-Only Words"))
        self.add_field(
            "",
            self.exclude_hiragana_only_checkbox,
            helper=self.tr(
                "Skip words written entirely in hiragana (e.g. する, これ). Focuses the deck on kanji vocabulary."
            ),
        )

        self.exclude_katakana_only_checkbox = QCheckBox(self.tr("Exclude Katakana-Only Words"))
        self.add_field(
            "",
            self.exclude_katakana_only_checkbox,
            helper=self.tr("Skip words written entirely in katakana (e.g. コーヒー)."),
        )

        # i+1 Sentence Filter section
        self.add_section(self.tr("i+1 Sentence Filter"))

        self.use_i_plus_one_checkbox = QCheckBox(self.tr("Only Mine i+1 Sentences"))
        self.use_i_plus_one_checkbox.setToolTip(
            self.tr(
                "Only mine words in a sentence with exactly one unknown word (i+1); "
                "overrides sentence deduplication."
            )
        )
        self.add_field("", self.use_i_plus_one_checkbox)

        # Sentence Length section (Issue #33)
        self.add_section(self.tr("Sentence Length"))

        self.use_sentence_length_checkbox = QCheckBox(self.tr("Enable Sentence Length Filter"))
        self.use_sentence_length_checkbox.setToolTip(
            self.tr(
                "Drop words whose example sentence exceeds the audio-duration "
                "or character caps below. Either cap set to 0 means no limit "
                "for that dimension. Reduces deck size and speeds up reviews."
            )
        )
        self.add_field("", self.use_sentence_length_checkbox)

        self.max_sentence_duration_spinbox = QDoubleSpinBox()
        self.max_sentence_duration_spinbox.setRange(0.0, 600.0)
        self.max_sentence_duration_spinbox.setDecimals(1)
        self.max_sentence_duration_spinbox.setSingleStep(0.5)
        self.max_sentence_duration_spinbox.setSuffix(self.tr(" s"))
        self.max_sentence_duration_spinbox.setSpecialValueText(self.tr("No limit"))
        self.add_field(
            self.tr("Max Sentence Duration"),
            self.max_sentence_duration_spinbox,
            helper=self.tr(
                "Drops cards whose example sentence audio is longer than this many seconds. " "Set to 0 for no limit."
            ),
        )

        self.max_sentence_chars_spinbox = QSpinBox()
        self.max_sentence_chars_spinbox.setRange(0, 1000)
        self.max_sentence_chars_spinbox.setSpecialValueText(self.tr("No limit"))
        self.add_field(
            self.tr("Max Sentence Characters"),
            self.max_sentence_chars_spinbox,
            helper=self.tr("Drops cards whose sentence text exceeds this many characters. Set to 0 for no limit."),
        )

        # Reading section: per-book minimum word occurrence (Reading tab).
        self.add_section(self.tr("Reading"))

        self.reading_min_occurrence_spinbox = QSpinBox()
        self.reading_min_occurrence_spinbox.setRange(1, 100)
        self.reading_min_occurrence_spinbox.setSpecialValueText(self.tr("Off"))
        self.add_field(
            self.tr("Minimum Word Occurrences"),
            self.reading_min_occurrence_spinbox,
            helper=self.tr(
                "Minimum number of times a word must appear in a book or volume to be "
                "mined. 1 = no minimum (filter off)."
            ),
        )

        # Card Formatting section (Issue #20)
        self.add_section(self.tr("Card Formatting"))

        self.bold_target_in_sentence_checkbox = QCheckBox(self.tr("Bold target word in sentence"))
        # QToolTip has no PlainText format and auto-detects HTML when the
        # string contains tag-like substrings. Escape the angle brackets so
        # the literal "<b>...</b>" markup is visible. Issue #20.
        self.bold_target_in_sentence_checkbox.setToolTip(
            self.tr(
                "Wrap the mined word in &lt;b&gt;...&lt;/b&gt; inside the Sentence and "
                "SentenceFurigana fields. Match is the exact MeCab span of the "
                "mined morpheme, so duplicated surfaces in a sentence only bold "
                "the actually-mined occurrence."
            )
        )
        self.add_field("", self.bold_target_in_sentence_checkbox)

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
            self.tr("Exclude Deck"),
            self.tr("Deck to exclude from known-words detection:"),
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

    # --- Max frequency rank ---

    def get_max_frequency_rank(self) -> int:
        """Return the max frequency rank value."""
        return self.max_frequency_spinbox.value()

    def set_max_frequency_rank(self, value: int) -> None:
        """Set the max frequency rank spinbox."""
        self.max_frequency_spinbox.setValue(value)

    # --- Known words DB ---

    def get_use_known_words_db(self) -> bool:
        """Return whether the local known-words DB is enabled."""
        return self.use_known_words_db_checkbox.isChecked()

    def set_use_known_words_db(self, value: bool) -> None:
        """Set the use-known-words-DB checkbox."""
        self.use_known_words_db_checkbox.setChecked(value)

    # --- Word lists ---

    def get_blacklist_path(self) -> Path | None:
        """Return the blacklist path (None when the field is empty)."""
        raw = self.blacklist_selector.get_path()
        return Path(raw) if raw else None

    def set_blacklist_path(self, value: Path | None) -> None:
        """Set the blacklist file selector (clears when ``value`` is None)."""
        self.blacklist_selector.set_path(str(value) if value else "")

    def get_use_blacklist(self) -> bool:
        """Return whether the blacklist is enabled."""
        return self.use_blacklist_checkbox.isChecked()

    def set_use_blacklist(self, value: bool) -> None:
        """Set the blacklist-enabled checkbox."""
        self.use_blacklist_checkbox.setChecked(value)

    def get_whitelist_path(self) -> Path | None:
        """Return the whitelist path (None when the field is empty)."""
        raw = self.whitelist_selector.get_path()
        return Path(raw) if raw else None

    def set_whitelist_path(self, value: Path | None) -> None:
        """Set the whitelist file selector (clears when ``value`` is None)."""
        self.whitelist_selector.set_path(str(value) if value else "")

    def get_use_whitelist(self) -> bool:
        """Return whether the whitelist is enabled."""
        return self.use_whitelist_checkbox.isChecked()

    def set_use_whitelist(self, value: bool) -> None:
        """Set the whitelist-enabled checkbox."""
        self.use_whitelist_checkbox.setChecked(value)

    # --- Subtitle regex ---

    def get_subtitle_regex_filter(self) -> str:
        """Return the subtitle regex pattern."""
        return self.subtitle_regex_edit.text()

    def set_subtitle_regex_filter(self, value: str) -> None:
        """Set the subtitle regex pattern field."""
        self.subtitle_regex_edit.setText(value)

    def get_subtitle_regex_replacement(self) -> str:
        """Return the subtitle regex replacement string."""
        return self.subtitle_replacement_edit.text()

    def set_subtitle_regex_replacement(self, value: str) -> None:
        """Set the subtitle regex replacement field."""
        self.subtitle_replacement_edit.setText(value)

    def get_use_subtitle_regex_filter(self) -> bool:
        """Return whether the subtitle regex filter is enabled."""
        return self.use_subtitle_regex_checkbox.isChecked()

    def set_use_subtitle_regex_filter(self, value: bool) -> None:
        """Set the subtitle regex filter checkbox."""
        self.use_subtitle_regex_checkbox.setChecked(value)

    # --- Deduplication ---

    def get_deduplicate_sentences(self) -> bool:
        """Return whether sentence deduplication is enabled."""
        return self.deduplicate_sentences_checkbox.isChecked()

    def set_deduplicate_sentences(self, value: bool) -> None:
        """Set the deduplicate-sentences checkbox."""
        self.deduplicate_sentences_checkbox.setChecked(value)

    # --- Script type ---

    def get_exclude_hiragana_only_words(self) -> bool:
        """Return whether hiragana-only words are excluded."""
        return self.exclude_hiragana_only_checkbox.isChecked()

    def set_exclude_hiragana_only_words(self, value: bool) -> None:
        """Set the exclude-hiragana-only checkbox."""
        self.exclude_hiragana_only_checkbox.setChecked(value)

    def get_exclude_katakana_only_words(self) -> bool:
        """Return whether katakana-only words are excluded."""
        return self.exclude_katakana_only_checkbox.isChecked()

    def set_exclude_katakana_only_words(self, value: bool) -> None:
        """Set the exclude-katakana-only checkbox."""
        self.exclude_katakana_only_checkbox.setChecked(value)

    # --- i+1 filter ---

    def get_use_i_plus_one_filter(self) -> bool:
        """Return whether the i+1 sentence filter is enabled."""
        return self.use_i_plus_one_checkbox.isChecked()

    def set_use_i_plus_one_filter(self, value: bool) -> None:
        """Set the i+1 filter checkbox."""
        self.use_i_plus_one_checkbox.setChecked(value)

    # --- Sentence length ---

    def get_use_sentence_length_filter(self) -> bool:
        """Return whether the sentence length filter is enabled."""
        return self.use_sentence_length_checkbox.isChecked()

    def set_use_sentence_length_filter(self, value: bool) -> None:
        """Set the sentence length filter checkbox."""
        self.use_sentence_length_checkbox.setChecked(value)

    def get_max_sentence_duration_seconds(self) -> float:
        """Return the max sentence duration (seconds)."""
        return self.max_sentence_duration_spinbox.value()

    def set_max_sentence_duration_seconds(self, value: float) -> None:
        """Set the max sentence duration spinbox."""
        self.max_sentence_duration_spinbox.setValue(value)

    def get_max_sentence_chars(self) -> int:
        """Return the max sentence character count."""
        return self.max_sentence_chars_spinbox.value()

    def set_max_sentence_chars(self, value: int) -> None:
        """Set the max sentence chars spinbox."""
        self.max_sentence_chars_spinbox.setValue(value)

    # --- Reading ---

    def get_reading_min_occurrence(self) -> int:
        """Return the per-book minimum word occurrence threshold."""
        return self.reading_min_occurrence_spinbox.value()

    def set_reading_min_occurrence(self, value: int) -> None:
        """Set the reading min-occurrence spinbox."""
        self.reading_min_occurrence_spinbox.setValue(value)

    # --- Card formatting ---

    def get_bold_target_in_sentence(self) -> bool:
        """Return whether the target word is bolded in the sentence field."""
        return self.bold_target_in_sentence_checkbox.isChecked()

    def set_bold_target_in_sentence(self, value: bool) -> None:
        """Set the bold-target-in-sentence checkbox."""
        self.bold_target_in_sentence_checkbox.setChecked(value)

    # --- Add-deck button enable/disable (used by AnkiProbeController) ---

    def set_add_deck_button_enabled(self, enabled: bool) -> None:
        """Enable or disable the Add Deck button."""
        self.add_deck_button.setEnabled(enabled)

    # ------------------------------------------------------------------
    # Config marshalling contract (OVH-019)
    # ------------------------------------------------------------------

    def load_from_config(self, config) -> None:
        """Populate all widgets from ``config``.

        Called by :meth:`SettingsTab._load_config` as part of the panel loop.
        Word-list selectors always set the value (including '' when the path is
        None) so Reset-to-Defaults clears a previously visible path (T-11).
        """
        self.set_max_frequency_rank(config.max_frequency_rank)
        # A cutoff with no enabled frequency source is inert (the pipeline skips
        # it). Surface that here so the setting doesn't look active. frequency_active
        # is derived from the enabled sources in the chain (Dictionaries tab).
        self.max_frequency_warning.setVisible(config.max_frequency_rank > 0 and not config.frequency_active)
        self.set_use_known_words_db(config.use_known_words_db)
        self.set_excluded_decks(config.excluded_decks)
        self.set_excluded_wordsets(config.excluded_wordsets)
        # T-11: always set (including '' for None) so Reset-to-Defaults clears
        # the selector; without this the stale path stays visible and the next
        # Save re-reads it back via get_path().
        self.set_blacklist_path(config.blacklist_path)
        self.set_use_blacklist(config.use_blacklist)
        self.set_whitelist_path(config.whitelist_path)
        self.set_use_whitelist(config.use_whitelist)
        self.set_subtitle_regex_filter(config.subtitle_regex_filter)
        self.set_subtitle_regex_replacement(config.subtitle_regex_replacement)
        self.set_use_subtitle_regex_filter(config.use_subtitle_regex_filter)
        self.set_deduplicate_sentences(config.deduplicate_sentences)
        self.set_exclude_hiragana_only_words(config.exclude_hiragana_only_words)
        self.set_exclude_katakana_only_words(config.exclude_katakana_only_words)
        self.set_use_i_plus_one_filter(config.use_i_plus_one_filter)
        self.set_use_sentence_length_filter(config.use_sentence_length_filter)
        self.set_max_sentence_duration_seconds(config.max_sentence_duration_seconds)
        self.set_max_sentence_chars(config.max_sentence_chars)
        self.set_reading_min_occurrence(config.reading_min_occurrence)
        self.set_bold_target_in_sentence(config.bold_target_in_sentence)

    def contribute(self, config):
        """Return a new config with this panel's fields applied.

        Uses ``dataclasses.replace`` so the frozen-config invariant is preserved.
        Called by :meth:`SettingsTab._on_save_clicked` as part of the contribute fold.

        Note: ``subtitle_regex_filter`` and ``use_subtitle_regex_filter`` are
        read here (behind the accessors) but the *validation* of the regex pattern
        stays in :meth:`SettingsTab._on_save_clicked` — it runs before the fold
        so any invalid pattern aborts Save before ``contribute`` is ever called.
        """
        return replace(
            config,
            max_frequency_rank=self.get_max_frequency_rank(),
            use_known_words_db=self.get_use_known_words_db(),
            excluded_decks=self.get_excluded_decks(),
            excluded_wordsets=self.get_excluded_wordsets(),
            blacklist_path=self.get_blacklist_path(),
            use_blacklist=self.get_use_blacklist(),
            whitelist_path=self.get_whitelist_path(),
            use_whitelist=self.get_use_whitelist(),
            subtitle_regex_filter=self.get_subtitle_regex_filter(),
            subtitle_regex_replacement=self.get_subtitle_regex_replacement(),
            use_subtitle_regex_filter=self.get_use_subtitle_regex_filter(),
            deduplicate_sentences=self.get_deduplicate_sentences(),
            exclude_hiragana_only_words=self.get_exclude_hiragana_only_words(),
            exclude_katakana_only_words=self.get_exclude_katakana_only_words(),
            use_i_plus_one_filter=self.get_use_i_plus_one_filter(),
            use_sentence_length_filter=self.get_use_sentence_length_filter(),
            max_sentence_duration_seconds=self.get_max_sentence_duration_seconds(),
            max_sentence_chars=self.get_max_sentence_chars(),
            reading_min_occurrence=self.get_reading_min_occurrence(),
            bold_target_in_sentence=self.get_bold_target_in_sentence(),
        )
