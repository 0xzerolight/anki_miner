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

from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.qt_helpers import (
    configure_data_view,
    data_row_height,
    install_copy_rows,
    reveal_settings,
)
from anki_miner.gui.widgets.base import FormPanel
from anki_miner.gui.widgets.enhanced import FileSelector
from anki_miner.services.wordset_service import load_wordset_catalog
from anki_miner.utils.i18n import tr_format
from anki_miner.utils.logging_ext import log_summary

logger = logging.getLogger(__name__)

# How tall the excluded-deck list is allowed to grow, in rows rather than
# pixels: a flat cap shows fewer decks the larger the user's text gets.
_EXCLUDED_DECK_ROWS = 5

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

    ANCHOR_NAMESPACE = "filtering"

    fetch_decks_requested = pyqtSignal()
    rebuild_known_words_requested = pyqtSignal()
    manage_known_words_requested = pyqtSignal()

    def __init__(self, parent=None):
        """Initialize the filtering settings panel."""
        # Most recently fetched deck names. Every picker open refreshes them
        # first because the connected endpoint or Anki collection may change.
        self._available_decks: list[str] = []
        super().__init__("Word Filtering", parent=parent)
        self._setup_fields()

    def _setup_fields(self) -> None:
        """Set up the panel fields."""
        # Word Frequency section. Frequency-source management lives on its own
        # settings page; only the rank band — a filter — stays here.
        self.add_section(self.tr("Word Frequency"))

        # The minimum and maximum are two ends of ONE filter, so they share one
        # row: as two stacked fields they read as unrelated settings and the
        # min<=max relationship stays invisible until the user trips over it.
        range_container = QWidget()
        range_row = QHBoxLayout(range_container)
        range_row.setContentsMargins(0, 0, 0, 0)
        range_row.setSpacing(SPACING.xs)

        self.min_frequency_spinbox = QSpinBox()
        self.min_frequency_spinbox.setRange(0, 100000)
        self.min_frequency_spinbox.setSpecialValueText(self.tr("No minimum"))
        # Tooltips sit on the spinboxes, not the row: add_field puts the helper on
        # the container, which these widgets cover with zero margins, and Qt
        # tooltips don't propagate to children (same fix as anki_settings_panel).
        self.min_frequency_spinbox.setToolTip(
            self.tr("Skip words more common than this rank - the ones already learned from exposure.")
        )

        self.max_frequency_spinbox = QSpinBox()
        self.max_frequency_spinbox.setRange(0, 100000)
        self.max_frequency_spinbox.setSpecialValueText(self.tr("No limit"))
        self.max_frequency_spinbox.setToolTip(self.tr("Skip words rarer than this rank."))

        # Match the two widths. Left to themselves they size to their own longest
        # special-value text ("No minimum" vs "No limit") and the row renders as
        # two mismatched boxes. A minimum (not a fixed width) so both still grow
        # with the user's text scale.
        band_width = max(self.min_frequency_spinbox.sizeHint().width(), self.max_frequency_spinbox.sizeHint().width())
        self.min_frequency_spinbox.setMinimumWidth(band_width)
        self.max_frequency_spinbox.setMinimumWidth(band_width)

        self.min_frequency_spinbox.valueChanged.connect(self._on_min_frequency_changed)
        self.max_frequency_spinbox.valueChanged.connect(self._on_max_frequency_changed)

        range_row.addWidget(self.min_frequency_spinbox)
        range_row.addWidget(QLabel(self.tr("to")))
        range_row.addWidget(self.max_frequency_spinbox)
        range_row.addStretch()

        self.add_field(
            self.tr("Frequency Rank Range"),
            range_container,
            helper=self.tr("Mine only words ranked inside this band. Rank 1 is the most common word."),
            anchor="frequency_rank_range",
            anchor_focus=self.min_frequency_spinbox,
            # Untranslated on purpose, like settings_search.LEGACY_DESTINATION_TERMS:
            # this is vocabulary users type, not text the app displays. The row was
            # labelled "Max Frequency Rank" until the minimum joined it.
            anchor_text=lambda: ("Min Frequency Rank", "Max Frequency Rank"),
        )

        # Unranked-word handling, explicit rather than inferred from which end is
        # set: dropping them is right for a maximum (they are not provably in the
        # top N) and wrong for a minimum (they are not provably common either),
        # so the user decides instead of the code guessing per end.
        self.keep_unranked_checkbox = QCheckBox(self.tr("Include Words Missing from the Frequency List"))
        self.keep_unranked_checkbox.setToolTip(
            self.tr(
                "Keep words that no loaded frequency source ranks. Off by default: a "
                "word with no rank cannot be shown to fall inside the band."
            )
        )
        self.add_field("", self.keep_unranked_checkbox)

        # Shown by load_from_config only when a band is set but no frequency
        # source is enabled. In that state the pipeline gates the cutoff off (it
        # would otherwise drop every word and create zero cards), so warn here
        # instead of letting the spinboxes look active, and link to the panel
        # that owns the frequency source chain.
        warning_row = QHBoxLayout()
        self.max_frequency_warning = QLabel(self.tr("No frequency source is loaded, so this range is ignored."))
        self.max_frequency_warning.setObjectName("helper-text")
        self.max_frequency_warning.setWordWrap(True)
        self.max_frequency_warning.setVisible(False)
        warning_row.addWidget(self.max_frequency_warning, 1)
        self.max_frequency_warning_action = QPushButton(self.tr("Open Frequency settings"))
        self.max_frequency_warning_action.clicked.connect(self._open_frequency_settings)
        self.max_frequency_warning_action.setVisible(False)
        warning_row.addWidget(self.max_frequency_warning_action)
        self.add_layout(warning_row)

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
            self.tr("Words in these decks (and their subdecks) stay mineable — not treated as already known.")
        )
        excluded_helper.setObjectName("helper-text")
        excluded_helper.setWordWrap(True)
        self.add_widget(excluded_helper)

        self.excluded_decks_list = QListWidget()
        self.excluded_decks_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        # Deck names are stored in the order the user added them; sorting is not
        # enabled. The cap is in rows so it still shows five decks at 150% text.
        configure_data_view(self.excluded_decks_list)
        install_copy_rows(self.excluded_decks_list)
        self.excluded_decks_list.setMaximumHeight(_EXCLUDED_DECK_ROWS * data_row_height(self.excluded_decks_list))
        self.add_widget(
            self.excluded_decks_list,
            anchor="excluded_decks",
            anchor_text=lambda: (excluded_helper.text(),),
        )

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
            # Built in a loop, so there is no panel attribute to derive an id
            # from; the catalog id is the stable name.
            self.add_field("", cb, anchor=f"wordset_{info.id}")

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
            anchor="subtitle_regex_presets",
            anchor_text=lambda: tuple(_preset_labels),
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
                "Skip words written entirely in hiragana (e.g. する, これ), including "
                "long-vowel spellings like すごーい. Focuses the deck on kanji vocabulary."
            ),
        )

        self.exclude_katakana_only_checkbox = QCheckBox(self.tr("Exclude Katakana-Only Words"))
        self.add_field(
            "",
            self.exclude_katakana_only_checkbox,
            helper=self.tr(
                "Skip words written entirely in katakana (e.g. コーヒー). Tick both boxes "
                "to also skip words mixing the two kana scripts (サボる, ヤバい)."
            ),
        )

        # Kana-variant fold: a kana-spelled word (うなずく) counts as known when
        # the kanji dictionary form (頷く) is already carded. Script-gated in
        # WordFilterService.filter_unknown; kanji variants never fold.
        self.match_kana_variants_checkbox = QCheckBox(self.tr("Treat Kana Spellings of Known Words as Known"))
        self.match_kana_variants_checkbox.setToolTip(
            self.tr(
                "When a subtitle spells a word in kana (e.g. うなずく) and the kanji "
                "dictionary form (頷く) is already in your collection or known list, "
                "skip it instead of creating a second card. Kanji spellings are "
                "never merged this way."
            )
        )
        self.add_field("", self.match_kana_variants_checkbox)

        # i+1 Sentence Filter section
        self.add_section(self.tr("i+1 Sentence Filter"))

        self.use_i_plus_one_checkbox = QCheckBox(self.tr("Only Mine i+1 Sentences"))
        self.use_i_plus_one_checkbox.setToolTip(
            self.tr(
                "Only mine words in a sentence with exactly one unknown word (i+1); overrides sentence deduplication."
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
                "Drops cards whose example sentence audio is longer than this many seconds. Set to 0 for no limit."
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
        """Fetch the current deck list, then open the picker.

        The connected endpoint or active Anki collection may have changed since
        the previous click, so an explicit Add Deck action never reuses names.
        :meth:`set_available_decks` opens the picker when the fetch completes.
        """
        self.fetch_decks_requested.emit()

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

    # --- Frequency rank band ---

    def _open_frequency_settings(self) -> None:
        reveal_settings(self, "frequency")

    def get_max_frequency_rank(self) -> int:
        """Return the max frequency rank value."""
        return self.max_frequency_spinbox.value()

    def set_max_frequency_rank(self, value: int) -> None:
        """Set the max frequency rank spinbox."""
        self.max_frequency_spinbox.setValue(value)

    def get_min_frequency_rank(self) -> int:
        """Return the most-common rank kept (0 = open end)."""
        return self.min_frequency_spinbox.value()

    def set_min_frequency_rank(self, value: int) -> None:
        """Set the min frequency rank spinbox."""
        self.min_frequency_spinbox.setValue(value)

    def get_frequency_keep_unranked(self) -> bool:
        """Return whether words with no frequency rank survive the band."""
        return self.keep_unranked_checkbox.isChecked()

    def set_frequency_keep_unranked(self, value: bool) -> None:
        """Set the unranked-words checkbox."""
        self.keep_unranked_checkbox.setChecked(value)

    def _on_min_frequency_changed(self, value: int) -> None:
        """Keep the band ordered: pushing the minimum past the maximum raises it.

        0 means "open end", not rank zero, so an open end is never dragged along.
        The sibling ``setValue`` re-enters the other handler exactly once, and
        that pass finds the band already ordered — it converges, it can't loop.
        """
        high = self.max_frequency_spinbox.value()
        if value > 0 and high > 0 and value > high:
            self.max_frequency_spinbox.setValue(value)
        self._sync_frequency_range_state()

    def _on_max_frequency_changed(self, value: int) -> None:
        """Keep the band ordered: pulling the maximum below the minimum lowers it."""
        low = self.min_frequency_spinbox.value()
        if value > 0 and low > 0 and value < low:
            self.min_frequency_spinbox.setValue(value)
        self._sync_frequency_range_state()

    def _sync_frequency_range_state(self) -> None:
        """Unranked-word handling only means anything while a bound is set."""
        band_set = self.min_frequency_spinbox.value() > 0 or self.max_frequency_spinbox.value() > 0
        self.keep_unranked_checkbox.setEnabled(band_set)

    # --- Known words DB ---

    def get_use_known_words_db(self) -> bool:
        """Return whether the local known-words DB is enabled."""
        return self.use_known_words_db_checkbox.isChecked()

    def set_use_known_words_db(self, value: bool) -> None:
        """Set the use-known-words-DB checkbox."""
        self.use_known_words_db_checkbox.setChecked(value)

    def get_match_kana_variants(self) -> bool:
        """Return whether kana spellings of known words count as known."""
        return self.match_kana_variants_checkbox.isChecked()

    def set_match_kana_variants(self, value: bool) -> None:
        """Set the kana-variant fold checkbox."""
        self.match_kana_variants_checkbox.setChecked(value)

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
        # Signals blocked while loading: a stored band with min > max (reachable
        # only by hand-editing gui_config.json) would otherwise have the clamp
        # silently rewrite the other end during a plain load.
        self.min_frequency_spinbox.blockSignals(True)
        self.max_frequency_spinbox.blockSignals(True)
        try:
            self.set_min_frequency_rank(config.min_frequency_rank)
            self.set_max_frequency_rank(config.max_frequency_rank)
        finally:
            self.min_frequency_spinbox.blockSignals(False)
            self.max_frequency_spinbox.blockSignals(False)
        self.set_frequency_keep_unranked(config.frequency_keep_unranked)
        self._sync_frequency_range_state()
        # A band with no enabled frequency source is inert (the pipeline skips
        # it). Surface that here so the setting doesn't look active. frequency_active
        # is derived from the enabled sources in the chain (Frequency panel).
        band_set = config.min_frequency_rank > 0 or config.max_frequency_rank > 0
        show_frequency_warning = band_set and not config.frequency_active
        self.max_frequency_warning.setVisible(show_frequency_warning)
        self.max_frequency_warning_action.setVisible(show_frequency_warning)
        if show_frequency_warning:
            log_summary(
                logger,
                "Filtering config degraded",
                level=logging.WARNING,
                reason="frequency_source_missing",
                min_frequency_rank=config.min_frequency_rank,
                max_frequency_rank=config.max_frequency_rank,
            )
        self.set_use_known_words_db(config.use_known_words_db)
        self.set_match_kana_variants(config.known_words_match_kana_variants)
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
        Called by :meth:`SettingsTab.commit_settings` as part of the contribute fold.

        Note: ``subtitle_regex_filter`` and ``use_subtitle_regex_filter`` are
        read here (behind the accessors) but the *validation* of the regex pattern
        stays in :meth:`SettingsTab.commit_settings` — it runs before the fold
        so any invalid pattern aborts Save before ``contribute`` is ever called.
        """
        return replace(
            config,
            min_frequency_rank=self.get_min_frequency_rank(),
            max_frequency_rank=self.get_max_frequency_rank(),
            frequency_keep_unranked=self.get_frequency_keep_unranked(),
            use_known_words_db=self.get_use_known_words_db(),
            known_words_match_kana_variants=self.get_match_kana_variants(),
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
