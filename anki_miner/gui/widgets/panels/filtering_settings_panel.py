"""Word filtering settings panel."""

import logging
from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.language_gate import apply_language_gate, field_row_widgets
from anki_miner.gui.utils.qt_helpers import (
    configure_data_view,
    data_row_height,
    install_copy_rows,
    reveal_settings,
)
from anki_miner.gui.widgets.base import FormPanel
from anki_miner.gui.widgets.enhanced import FileSelector
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages.profile import ScriptFilterOption
from anki_miner.languages.registry import config_language, get_profile
from anki_miner.services.wordset_service import load_wordset_catalog
from anki_miner.utils.i18n import tr_format
from anki_miner.utils.logging_ext import log_summary

logger = logging.getLogger(__name__)

#: tr-context for the option-driven script-filter rows. Spelled explicitly
#: because the label reaches the panel as data: ``self.tr(option.label)`` reads
#: fine and works at runtime, but pylupdate parses the source rather than
#: running it, so the English never reaches a catalogue. The literals below are
#: what gets extracted; a contract test pins each one equal to its option's
#: ``label`` so the two cannot drift into a silent lookup miss.
_TR_CONTEXT = "FilteringSettingsPanel"

#: ``option_id`` -> extractable English label, for every script-filter option
#: whose language has no hand-built rows of its own. An option missing here
#: falls back to its own ``label`` (English, untranslated) rather than to a
#: blank checkbox.
SCRIPT_FILTER_LABELS: dict[str, str] = {
    "hangul_only": QT_TRANSLATE_NOOP("FilteringSettingsPanel", "Exclude hangul-only words"),
    "hanja_containing": QT_TRANSLATE_NOOP("FilteringSettingsPanel", "Exclude words containing hanja"),
}

#: Help text per option, keyed the same way. Separate from the label because
#: ``ScriptFilterOption`` carries no helper of its own -- it is a mining-side
#: contract, and tooltip prose is a GUI concern.
SCRIPT_FILTER_HELPERS: dict[str, str] = {
    "hangul_only": QT_TRANSLATE_NOOP(
        "FilteringSettingsPanel",
        "Skip words written entirely in hangul. Leaves the deck to words written with hanja.",
    ),
    "hanja_containing": QT_TRANSLATE_NOOP(
        "FilteringSettingsPanel",
        "Skip words that contain any hanja character, keeping the deck to plain hangul vocabulary.",
    ),
}

#: Capabilities whose profile supplies the option-driven script-filter rows.
#: Japanese keeps its own hand-built Script Type combo under ``kana_filters``
#: (C12): its four items are the four states of the two booleans, and the
#: field-less third option (``mixed_kana_only``) is the "both on" item. A combo
#: built from Korean's options could not show a saved "both on" state, which is
#: why Korean keeps its option-driven checkboxes.
_OPTION_DRIVEN_FILTER_CAPABILITIES = ("hangul_filters",)


def _capability_script_filter_options(capabilities: tuple[str, ...]) -> tuple[ScriptFilterOption, ...]:
    """Script-filter options declared by the registered profiles with any of *capabilities*.

    Resolved from the registry rather than from a language code, and NOT from
    ``available_mining_languages`` -- that one drops a language whose engine is
    missing, and a config already set to it would then gate rows into view that
    were never built. Options with no config field of their own are skipped:
    there is no boolean for a checkbox to write.
    """
    options: list[ScriptFilterOption] = []
    for capability in capabilities:
        for code in AVAILABLE_LANGUAGES:
            try:
                profile = get_profile(code)
            except (LookupError, ValueError, ImportError) as exc:
                logger.debug("No profile for %r while building script-filter rows: %s", code, exc)
                continue
            if capability in profile.capabilities:
                options.extend(opt for opt in profile.script.filter_options() if opt.config_field)
                break
    return tuple(options)


# How tall the excluded-deck list is allowed to grow, in rows rather than
# pixels: a flat cap shows fewer decks the larger the user's text gets.
_EXCLUDED_DECK_ROWS = 5


class FilteringSettingsPanel(FormPanel):
    """Panel for word filtering settings.

    Provides:
    - Word frequency filtering options
    - Known-words database toggle and deck exclusions (Issue #38); the cache
      rebuild lives in the Manage Known Words dialog (C13)

    Signals:
        fetch_decks_requested: Emitted when the deck list must be fetched from
            AnkiConnect to populate the "Add Deck…" picker.
        manage_known_words_requested: Emitted when the user opens the Manage
            Known Words dialog (Issue #42).
    """

    ANCHOR_NAMESPACE = "filtering"

    fetch_decks_requested = pyqtSignal()
    manage_known_words_requested = pyqtSignal()

    def __init__(self, parent=None):
        """Initialize the filtering settings panel."""
        # Most recently fetched deck names. Every picker open refreshes them
        # first because the connected endpoint or Anki collection may change.
        self._available_decks: list[str] = []
        super().__init__(self.tr("Word Filters"), parent=parent)
        self._setup_fields()

    def _setup_fields(self) -> None:
        """Set up the panel fields."""
        # Every capability contributor extends this list; Stage 2B adds the
        # non-ja rows to the same one. A second assignment would drop these
        # pairs, so this is the only place it is bound.
        self._language_gate_pairs: list[tuple[QWidget, str]] = []

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

        self.use_known_words_db_checkbox = QCheckBox(self.tr("Keep words known after their cards are deleted"))
        self.use_known_words_db_checkbox.setToolTip(
            self.tr(
                "Words stay known after their Anki cards are deleted or moved to an "
                "excluded deck. Rebuild (in Manage Known Words) forgets them."
            )
        )
        self.add_field("", self.use_known_words_db_checkbox)

        # Rebuild (clearing the additive local cache so deck exclusions take
        # effect, Issue #38) sits in the Manage Known Words dialog beside the
        # "cached from Anki" count it clears (C13).
        known_words_row = QHBoxLayout()

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
        known_words_row.addWidget(self.manage_known_words_button)
        known_words_row.addStretch()
        self.add_layout(known_words_row)

        # Kana-variant fold: a kana-spelled word (うなずく) counts as known when
        # the kanji dictionary form (頷く) is already carded. Script-gated in
        # WordFilterService.filter_unknown; kanji variants never fold. Lives here
        # rather than under Script Type because it is a known-words rule, not a
        # script-exclusion filter.
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
        # C09: an empty list is one line, not an empty bordered box.
        self.excluded_decks_empty_label = QLabel(self.tr("No decks excluded."))
        self.excluded_decks_empty_label.setObjectName("helper-text")
        excluded_container = QWidget()
        excluded_layout = QVBoxLayout(excluded_container)
        excluded_layout.setContentsMargins(0, 0, 0, 0)
        excluded_layout.addWidget(self.excluded_decks_list)
        excluded_layout.addWidget(self.excluded_decks_empty_label)
        # The anchor is the container, so search still finds the row while the
        # list itself is hidden (empty).
        self.add_widget(
            excluded_container,
            anchor="excluded_decks",
            anchor_focus=self.excluded_decks_list,
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
        self.excluded_decks_list.itemSelectionChanged.connect(self._sync_excluded_decks)
        list_model = self.excluded_decks_list.model()
        if list_model is not None:
            list_model.rowsInserted.connect(self._sync_excluded_decks)
            list_model.rowsRemoved.connect(self._sync_excluded_decks)
            # clear() resets the model without a rowsRemoved.
            list_model.modelReset.connect(self._sync_excluded_decks)
        self._sync_excluded_decks()

        # Word Lists section. The chosen file IS the switch (D15 item 1):
        # choosing a file turns the list on, clearing it turns it off. The two
        # use_* config fields stay, derived from the field on every save.
        self.add_section(self.tr("Word Lists"))

        self.blacklist_selector = FileSelector(
            label="", file_mode=True, placeholder=self.tr("Select blacklist file...")
        )
        self.add_field(
            self.tr("Blacklist File"),
            self.blacklist_selector,
            helper=self.tr("Text file with one word per line to always skip. Leave empty to skip nothing."),
            anchor_text=lambda: ("Enable Blacklist",),
        )

        self.whitelist_selector = FileSelector(
            label="", file_mode=True, placeholder=self.tr("Select whitelist file...")
        )
        self.add_field(
            self.tr("Whitelist File"),
            self.whitelist_selector,
            helper=self.tr(
                "Text file with one word per line to force-include, bypassing frequency, "
                "script, length and other filters. A word must still have a dictionary entry "
                "and not already be in Anki or your known-words list. Leave empty to force nothing."
            ),
            anchor_text=lambda: ("Enable Whitelist",),
        )

        # Name Wordsets section (Issue #59). Bundled proper-noun lists derived
        # from JMnedict; checking one excludes those names from mining. Catches
        # names unidic-lite mistags as common nouns (the POS filter only drops
        # proper nouns the parser actually recognizes as 固有名詞).
        self.add_section(self.tr("Name Wordsets"))
        # Captured while it is the active heading: the language gate hides the
        # divider and its helper with the rows under them, so a language
        # without name wordsets is not left with an empty section.
        self._wordset_section_label = self._active_section_label

        self._wordsets_helper = QLabel(
            self.tr(
                "Exclude bundled lists of Japanese people, place and company names "
                "from mining. Whitelisted names are still mined."
            )
        )
        self._wordsets_helper.setObjectName("helper-text")
        self._wordsets_helper.setWordWrap(True)
        self.add_widget(self._wordsets_helper)

        # One box for all four bundled lists (D15 item 2). Checked = every
        # list is excluded, unchecked = none; a subset saved before this change
        # shows partly checked until the user clicks, and then means "all".
        catalog = load_wordset_catalog()
        self._wordset_ids: tuple[str, ...] = tuple(info.id for info in catalog)
        self._partial_wordsets: tuple[str, ...] = ()
        self.names_checkbox = QCheckBox(self.tr("Skip names of people, places and companies"))
        self.names_checkbox.setToolTip(
            tr_format(
                self.tr("Excludes the bundled name lists from mining: %1."),
                ", ".join(f"{info.label} ({info.count:,})" for info in catalog),
            )
        )
        self.names_checkbox.clicked.connect(self._on_names_clicked)
        self.add_field(
            "",
            self.names_checkbox,
            anchor_text=lambda: tuple(info.label for info in catalog),
        )

        # Script Type section (Issue #57)
        self.add_section(self.tr("Script Type"))
        self._script_type_section_label = self._active_section_label

        # One choice over the two kana booleans (C12). Both on also skips words
        # that mix the two kana scripts (サボる, ヤバい): ja's third filter
        # option, mixed_kana_only, has no field of its own, which is why it was
        # invisible as two checkboxes.
        self.script_type_combo = QComboBox()
        self.script_type_combo.addItem(self.tr("Keep all words"), "keep")
        self.script_type_combo.addItem(self.tr("Skip hiragana-only words"), "hiragana")
        self.script_type_combo.setItemData(
            1,
            self.tr(
                "Skip words written entirely in hiragana (e.g. する, これ), including "
                "long-vowel spellings like すごーい. Focuses the deck on kanji vocabulary."
            ),
            Qt.ItemDataRole.ToolTipRole,
        )
        self.script_type_combo.addItem(self.tr("Skip katakana-only words"), "katakana")
        self.script_type_combo.setItemData(
            2, self.tr("Skip words written entirely in katakana (e.g. コーヒー)."), Qt.ItemDataRole.ToolTipRole
        )
        self.script_type_combo.addItem(self.tr("Skip all kana-only words (including mixed)"), "all_kana")
        self.script_type_combo.setItemData(
            3,
            self.tr("Skip every word written without kanji, including words that mix hiragana and katakana."),
            Qt.ItemDataRole.ToolTipRole,
        )
        self.add_field("", self.script_type_combo, anchor_text=self._script_type_search_text)

        # Option-driven script filters. The Korean pair binds to the SAME two
        # language-scoped booleans the Script Type combo above uses, and the
        # mapping is counter-intuitive (hangul-only -> exclude_hiragana_only_words), so the
        # binding is taken from the profile's own options instead of restated
        # here: the panel and WordFilterService then read one source of truth.
        # It gets a heading of its own because "Script Type" above is gated on
        # kana_filters and hides here, which would leave these rows reading as
        # part of the section above.
        self.add_section(self.tr("Script Type"))
        self._script_filter_section_label = self._active_section_label

        self.script_filter_checkboxes: dict[str, QCheckBox] = {}
        self._script_filter_fields: dict[str, str] = {}
        for option in _capability_script_filter_options(_OPTION_DRIVEN_FILTER_CAPABILITIES):
            label = SCRIPT_FILTER_LABELS.get(option.option_id, option.label)
            helper = SCRIPT_FILTER_HELPERS.get(option.option_id, "")
            checkbox = QCheckBox(QCoreApplication.translate(_TR_CONTEXT, label))
            self.add_field(
                "",
                checkbox,
                helper=QCoreApplication.translate(_TR_CONTEXT, helper) if helper else "",
                # Loop-built, so there is no panel attribute to derive from.
                anchor=f"script_filter_{option.option_id}",
            )
            self.script_filter_checkboxes[option.option_id] = checkbox
            self._script_filter_fields[option.option_id] = option.config_field

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

        # Language-gated rows. Each row contributes its label too, so a hidden
        # field never leaves a dangling caption behind.
        self._language_gate_pairs.extend(
            (w, "kana_filters")
            for cb in (self.script_type_combo, self.match_kana_variants_checkbox)
            for w in field_row_widgets(self, cb)
        )
        if self._script_type_section_label is not None:
            self._language_gate_pairs.append((self._script_type_section_label, "kana_filters"))
        # The option-driven rows join the same list. This is a cross product,
        # not a per-option pairing: every checkbox here is paired with EVERY
        # capability in _OPTION_DRIVEN_FILTER_CAPABILITIES, because
        # _capability_script_filter_options() pools options from all of them
        # without keeping each option's originating capability. apply_language_gate
        # calls setVisible(cap in capabilities) per pair in order, so with a
        # second entry the last capability's setVisible call would win for
        # every checkbox regardless of which capability actually supplied it.
        # Harmless today because there is exactly one entry. Before adding a
        # second, pair each option with its own capability at collection time
        # instead of this cross product. EXTENDED, never assigned, like every
        # block in this method.
        self._language_gate_pairs.extend(
            (w, capability)
            for capability in _OPTION_DRIVEN_FILTER_CAPABILITIES
            for cb in self.script_filter_checkboxes.values()
            for w in field_row_widgets(self, cb)
        )
        if self._script_filter_section_label is not None:
            self._language_gate_pairs.extend(
                (self._script_filter_section_label, capability) for capability in _OPTION_DRIVEN_FILTER_CAPABILITIES
            )
        self._language_gate_pairs.extend((w, "name_wordsets") for w in field_row_widgets(self, self.names_checkbox))
        self._language_gate_pairs.extend(
            (w, "name_wordsets") for w in (self._wordset_section_label, self._wordsets_helper) if w is not None
        )

        self.add_stretch()

    #: combo item data -> (exclude_hiragana_only_words, exclude_katakana_only_words).
    _SCRIPT_TYPE_VALUES: dict[str, tuple[bool, bool]] = {
        "keep": (False, False),
        "hiragana": (True, False),
        "katakana": (False, True),
        "all_kana": (True, True),
    }

    def _script_type_search_text(self) -> tuple[str, ...]:
        """Searchable text for the label-less Script Type combo: items, tips, script names."""
        parts: list[str] = ["hiragana", "katakana", "kana"]
        combo = self.script_type_combo
        for index in range(combo.count()):
            parts.append(combo.itemText(index))
            tooltip = combo.itemData(index, Qt.ItemDataRole.ToolTipRole)
            if tooltip:
                parts.append(str(tooltip))
        return tuple(parts)

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

    def _sync_excluded_decks(self, *_args) -> None:
        """Empty: one line and Add only. Otherwise the list, and Remove for a selection (C09)."""
        empty = self.excluded_decks_list.count() == 0
        self.excluded_decks_list.setVisible(not empty)
        self.excluded_decks_empty_label.setVisible(empty)
        self.remove_deck_button.setVisible(not empty)
        self.remove_deck_button.setEnabled(bool(self.excluded_decks_list.selectedItems()))

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
        """The excluded name-list ids, in catalog order (D15 item 2)."""
        state = self.names_checkbox.checkState()
        if state == Qt.CheckState.Checked:
            return self._wordset_ids
        if state == Qt.CheckState.PartiallyChecked:
            return tuple(set_id for set_id in self._wordset_ids if set_id in self._partial_wordsets)
        return ()

    def set_excluded_wordsets(self, ids: tuple[str, ...]) -> None:
        """Show ``ids``: all -> checked, none -> unchecked, a subset -> partly checked."""
        wanted = tuple(set_id for set_id in self._wordset_ids if set_id in set(ids))
        if wanted and len(wanted) == len(self._wordset_ids):
            self.names_checkbox.setTristate(False)
            self.names_checkbox.setCheckState(Qt.CheckState.Checked)
        elif not wanted:
            self.names_checkbox.setTristate(False)
            self.names_checkbox.setCheckState(Qt.CheckState.Unchecked)
        else:
            self._partial_wordsets = wanted
            self.names_checkbox.setTristate(True)
            self.names_checkbox.setCheckState(Qt.CheckState.PartiallyChecked)

    def _on_names_clicked(self, _checked: bool) -> None:
        """A click is a real choice: leave the partial state for good.

        Qt has already moved a partial box to checked (its tristate cycle is
        unchecked -> partial -> checked); dropping tristate keeps a later click
        from ever landing on "partial" again.
        """
        self.names_checkbox.setTristate(False)

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
        """The blacklist is on exactly when a file is chosen (D15 item 1)."""
        return self.get_blacklist_path() is not None

    def get_whitelist_path(self) -> Path | None:
        """Return the whitelist path (None when the field is empty)."""
        raw = self.whitelist_selector.get_path()
        return Path(raw) if raw else None

    def set_whitelist_path(self, value: Path | None) -> None:
        """Set the whitelist file selector (clears when ``value`` is None)."""
        self.whitelist_selector.set_path(str(value) if value else "")

    def get_use_whitelist(self) -> bool:
        """The whitelist is on exactly when a file is chosen (D15 item 1)."""
        return self.get_whitelist_path() is not None

    # --- Script type ---

    def get_exclude_hiragana_only_words(self) -> bool:
        """Whether hiragana-only words are skipped (Script Type combo)."""
        return self._SCRIPT_TYPE_VALUES[self.script_type_combo.currentData()][0]

    def get_exclude_katakana_only_words(self) -> bool:
        """Whether katakana-only words are skipped (Script Type combo)."""
        return self._SCRIPT_TYPE_VALUES[self.script_type_combo.currentData()][1]

    def set_script_type(self, hiragana_only: bool, katakana_only: bool) -> None:
        """Select the combo item for the two stored booleans."""
        value = next(key for key, pair in self._SCRIPT_TYPE_VALUES.items() if pair == (hiragana_only, katakana_only))
        self.script_type_combo.setCurrentIndex(self.script_type_combo.findData(value))

    # --- Reading ---

    def get_reading_min_occurrence(self) -> int:
        """Return the per-book minimum word occurrence threshold."""
        return self.reading_min_occurrence_spinbox.value()

    def set_reading_min_occurrence(self, value: int) -> None:
        """Set the reading min-occurrence spinbox."""
        self.reading_min_occurrence_spinbox.setValue(value)

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
        # D15 item 1: a stored path whose switch was off is shown empty, so it
        # stays off -- and the next save drops the path with it.
        self.set_blacklist_path(config.blacklist_path if config.use_blacklist else None)
        self.set_whitelist_path(config.whitelist_path if config.use_whitelist else None)
        self.set_script_type(bool(config.exclude_hiragana_only_words), bool(config.exclude_katakana_only_words))
        # Same two booleans, read through whichever language's option named them.
        for option_id, checkbox in self.script_filter_checkboxes.items():
            checkbox.setChecked(bool(getattr(config, self._script_filter_fields[option_id])))
        self.set_reading_min_occurrence(config.reading_min_occurrence)
        apply_language_gate(self._language_gate_pairs, get_profile(config_language(config)).capabilities)

    def contribute(self, config):
        """Return a new config with this panel's fields applied.

        Uses ``dataclasses.replace`` so the frozen-config invariant is preserved.
        Called by :meth:`SettingsTab.commit_settings` as part of the contribute fold.
        """
        updated = replace(
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
            exclude_hiragana_only_words=self.get_exclude_hiragana_only_words(),
            exclude_katakana_only_words=self.get_exclude_katakana_only_words(),
            reading_min_occurrence=self.get_reading_min_occurrence(),
        )
        # Language-scoped rows contribute only while their capability is present.
        # Every option-driven Script Type row reuses the two JA-historical
        # fields (exclude_hiragana_only_words, exclude_katakana_only_words) as
        # generic slots -- Korean's hangul-only and hanja-containing checkboxes
        # among them -- and at most one language's row set is ever visible, so
        # a blind write would stamp a hidden checkbox's stale value onto a
        # language that has neither setting. Visibility is the gate's own
        # output, so there is one source of truth for "does this language have
        # this setting".
        # The Script Type combo above already wrote these two fields unconditionally --
        # under another language they are hidden and still hold the loaded
        # value, so that write is a no-op. The visible option-driven row is the
        # one the user can actually reach, so it wins.
        for option_id, checkbox in self.script_filter_checkboxes.items():
            if checkbox.isVisibleTo(self):
                updated = replace(updated, **{self._script_filter_fields[option_id]: checkbox.isChecked()})
        return updated
