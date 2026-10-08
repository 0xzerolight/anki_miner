"""Sentence-content settings panel.

Moved off Filtering (now "Word Filters"): these rows shape what the example
sentence itself looks like -- text cleanup, a second subtitle track, whole
sentences instead of fragments, and bolding the mined word -- rather than
which words get mined. "Colour the reading by tone" moved off Filtering too
(T10), onto Cards & Anki, not here -- it colours a card field, not a sentence.
Sentence rule and sentence length moved here from Word Filters (C13, UI/UX
audit 2026-09-29).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLineEdit,
    QSpinBox,
    QWidget,
)

from anki_miner.gui.widgets.base import FormPanel
from anki_miner.gui.widgets.base.disclosure import make_disclosure
from anki_miner.languages.registry import config_language, get_profile

#: Built-in cleanups for a language whose profile ships no pattern of its own
#: (ja, zh, yue). "Remove speaker names, sound effects and music notes" makes
#: sure each one is in the pattern. The labels are no longer shown. Patterns
#: target both half-width and full-width punctuation common in JP subtitle files.
SUBTITLE_REGEX_PRESETS: tuple[tuple[str, str], ...] = (
    ("Parens (Tanaka)", r"\([^)]*\)|（[^）]*）"),
    ("Brackets [SFX]", r"\[[^\]]*\]|［[^］]*］"),
    ("Music ♪♬", r"[♪♬♫#～〜]+"),
    ("Speaker: prefix", r"^[^「『:：]+[:：]\s*"),
    # A dash that opens a speaker turn ("- Hi. - Hello."): at the line start or after
    # a sentence terminator. Hyphenated words and a mid-sentence dash are untouched.
    ("Dialogue dash", r"(?:^|(?<=[.!?…]\s))[-–—]\s+"),
)


def builtin_cleanup_pieces(language_default: str) -> tuple[str, ...]:
    """The pieces the one cleanup box makes sure are in the pattern (D15 extension).

    A language that ships its own pattern (ko, th and the spaced languages set
    ``subtitle_regex_filter`` in their scoped defaults) keeps exactly that
    pattern as one piece; the rest get the five presets above.
    """
    if language_default:
        return (language_default,)
    return tuple(pattern for _label, pattern in SUBTITLE_REGEX_PRESETS)


def add_missing_pieces(pattern: str, pieces: Sequence[str]) -> str:
    """Append every piece not already inside ``pattern``, joined with ``|``.

    A user pattern that already holds some presets (from the old per-preset
    buttons, or typed) keeps its text and order; only what is missing is added.
    """
    current = pattern.strip()
    for piece in pieces:
        if piece in current:
            continue
        current = f"{current}|{piece}" if current else piece
    return current


def cleanup_state(use: bool, pattern: str, pieces: Sequence[str]) -> Qt.CheckState:
    """The box's state, derived from the two stored fields -- nothing new is stored.

    Off -> unchecked. On with every built-in piece present -> checked (extra
    text of the user's own is fine). On with a pattern missing some -> partly
    checked: the user's own pattern is in use.
    """
    if not use:
        return Qt.CheckState.Unchecked
    if all(piece in pattern for piece in pieces):
        return Qt.CheckState.Checked
    return Qt.CheckState.PartiallyChecked


class SentencesSettingsPanel(FormPanel):
    """Panel for example-sentence content settings.

    Provides:
    - Subtitle text cleanup: one plain-language box, the raw regex behind a disclosure (D15 extension)
    - Sentence rule (dedup / i+1) and sentence length caps (C13)
    - Secondary-language subtitles (F7)
    - Whole-sentence merging across subtitle lines
    - Bolding the mined word in the sentence
    """

    ANCHOR_NAMESPACE = "sentences"

    def __init__(self, parent=None):
        """Initialize the sentences settings panel."""
        super().__init__(self.tr("Sentences"), parent=parent)
        self._cleanup_pieces: tuple[str, ...] = builtin_cleanup_pieces("")
        self._setup_fields()

    def _setup_fields(self) -> None:
        """Set up the panel fields."""
        # Clean up subtitle text (Issue #8; D15 extension). One plain-language
        # box does the common case; the raw regex is for experts, behind a
        # disclosure. The box's state is derived from the stored toggle and
        # pattern (cleanup_state), so no new setting exists.
        self.add_section(self.tr("Clean up subtitle text"))

        self.use_subtitle_regex_checkbox = QCheckBox(self.tr("Remove speaker names, sound effects and music notes"))
        self.use_subtitle_regex_checkbox.clicked.connect(self._on_cleanup_clicked)
        self.add_field(
            "",
            self.use_subtitle_regex_checkbox,
            helper=self.tr(
                "Removes (notes), [sound effects], ♪ music and dialogue dashes from each subtitle line "
                "before mining, and speaker labels where the script marks them. Half-checked means your "
                "own pattern is in use: click to add every built-in cleanup to it."
            ),
            anchor_text=lambda: ("Enable Subtitle Regex Filter", "clean subtitles", "speaker labels"),
        )

        self.subtitle_regex_edit = QLineEdit()
        self.subtitle_regex_edit.setPlaceholderText(r"e.g. \([^)]*\)|\[[^\]]*\]")
        self.subtitle_regex_edit.setToolTip(
            self.tr(
                "Python regex matched in subtitle text and removed (or replaced) before mining. "
                "Useful for stripping speaker names like (Tanaka) or sound descriptions like [door]. "
                "Combine alternatives with |. Test patterns at https://regex101.com."
            )
        )
        self.subtitle_regex_edit.textEdited.connect(lambda _text: self._sync_cleanup_box())

        self.subtitle_replacement_edit = QLineEdit()
        self.subtitle_replacement_edit.setPlaceholderText(self.tr("(empty = delete match)"))
        self.subtitle_replacement_edit.setToolTip(
            self.tr(
                "Inserted in place of each match (empty deletes it). Use Python "
                "backreferences \\1 \\2, not asbplayer's $1 $2."
            )
        )

        # The raw fields sit behind a disclosure, like Cards & Anki's marker
        # names. It opens by itself on load when a custom pattern is stored
        # (see _open_disclosure_for_custom_pattern).
        self._subtitle_regex_body = QWidget()
        body_form = QFormLayout(self._subtitle_regex_body)
        body_form.setContentsMargins(0, 0, 0, 0)
        body_form.addRow(self.tr("Regex Filter:"), self.subtitle_regex_edit)
        body_form.addRow(self.tr("Replacement:"), self.subtitle_replacement_edit)
        self.subtitle_regex_group = make_disclosure(self.tr("Edit the pattern (advanced)"), self._subtitle_regex_body)
        self.add_widget(self.subtitle_regex_group)
        # Registered by hand so the result is titled "Regex Filter" and keeps
        # its old id; a jump lands on the disclosure, which Space opens.
        self.register_setting(
            "subtitle_regex_edit",
            self.subtitle_regex_group,
            lambda: (
                self.tr("Regex Filter"),
                self.subtitle_regex_group.title(),
                self.tr("Replacement"),
                self.subtitle_regex_edit.toolTip(),
                self.subtitle_replacement_edit.toolTip(),
                self.tr("Clean up subtitle text"),
                self.tr("Sentences"),
            ),
            focus=self.subtitle_regex_group,
        )

        # Sentence options (C13): what the example sentence is and looks like.
        # Sentence rule and length moved here from Word Filters; Secondary
        # Subtitles, Full Sentences and Card Formatting were one row each and
        # join the same group.
        self.add_section(self.tr("Sentence options"))

        # Folds the old "Deduplicate by Sentence" and "Only Mine i+1 Sentences"
        # checkboxes into one choice -- i+1 already overrode dedup in
        # EpisodeProcessor, so the pair never expressed four states.
        self.sentence_rule_combo = QComboBox()
        self.sentence_rule_combo.addItem(self.tr("Mine every unknown word"), "all")
        self.sentence_rule_combo.addItem(self.tr("One card per sentence"), "dedup")
        self.sentence_rule_combo.setItemData(
            1,
            self.tr(
                "Mines at most one word per example sentence — the first one found in that sentence. "
                "Every other word sharing it is skipped."
            ),
            Qt.ItemDataRole.ToolTipRole,
        )
        self.sentence_rule_combo.addItem(self.tr("Only i+1 sentences (exactly one unknown word)"), "i_plus_one")
        self.sentence_rule_combo.setItemData(
            2,
            self.tr(
                "Only mine words in a sentence with exactly one unknown word (i+1); overrides sentence deduplication."
            ),
            Qt.ItemDataRole.ToolTipRole,
        )
        self.add_field(
            self.tr("Sentence Rule"),
            self.sentence_rule_combo,
            anchor_text=self._sentence_rule_search_text,
        )

        # No master toggle (Issue #33): each cap is off at 0, which its
        # "No limit" special value says -- the old helper line is gone (C13).
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

        # Secondary Subtitles (F7). One toggle gates every new surface.
        self.secondary_subtitle_checkbox = QCheckBox(self.tr("Enable secondary-language subtitles"))
        self.add_field(
            "",
            self.secondary_subtitle_checkbox,
            helper=self.tr(
                "Adds a translation subtitle picker and its own offset to the Video screens (Single, Batch, "
                "Deck Builder). Its line shows under the mining-language line in the Word Curator preview and, "
                "when the Translation field is mapped (Cards & Anki), on the card."
            ),
        )

        # Full Sentences (FUTURE_IDEAS 6).
        self.merge_incomplete_cues_checkbox = QCheckBox(self.tr("Mine full sentences across subtitle lines"))
        self.add_field(
            "",
            self.merge_incomplete_cues_checkbox,
            helper=self.tr(
                "Joins neighbouring subtitle lines when a line does not end a sentence, so the "
                "card carries the whole sentence instead of a fragment. Reading sources have no "
                "subtitle timings and ignore it."
            ),
        )

        # Bold target (Issue #20). QToolTip auto-detects HTML, so the angle
        # brackets are escaped to show the literal "<b>...</b>" markup.
        self.bold_target_in_sentence_checkbox = QCheckBox(self.tr("Bold target word in sentence"))
        self.bold_target_in_sentence_checkbox.setToolTip(
            self.tr(
                "Wrap the mined word in &lt;b&gt;...&lt;/b&gt; inside the sentence "
                "fields. Match is the exact span that was mined, so duplicated "
                "surfaces in a sentence only bold the actually-mined occurrence."
            )
        )
        self.add_field("", self.bold_target_in_sentence_checkbox)

        self.add_stretch()

    def _on_cleanup_clicked(self, _checked: bool) -> None:
        """A click is a choice: checked adds every missing built-in piece (D15 extension).

        Qt has already moved the box: a partly checked box goes to checked, a
        checked one to unchecked. Tristate is dropped so a later click never
        lands on "partly" again; _sync_cleanup_box re-derives it from the text.
        """
        box = self.use_subtitle_regex_checkbox
        box.setTristate(False)
        if box.checkState() == Qt.CheckState.Checked:
            self.subtitle_regex_edit.setText(add_missing_pieces(self.subtitle_regex_edit.text(), self._cleanup_pieces))
        self._sync_cleanup_box()

    def _sync_cleanup_box(self) -> None:
        """Re-derive the box from the toggle and the pattern text (never stores anything)."""
        box = self.use_subtitle_regex_checkbox
        use = box.checkState() != Qt.CheckState.Unchecked
        state = cleanup_state(use, self.subtitle_regex_edit.text(), self._cleanup_pieces)
        box.setTristate(state == Qt.CheckState.PartiallyChecked)
        box.setCheckState(state)

    def _open_disclosure_for_custom_pattern(self) -> None:
        """Show the raw fields when the stored pattern is not exactly the built-ins."""
        pattern = self.subtitle_regex_edit.text().strip()
        custom = bool(pattern) and pattern != "|".join(self._cleanup_pieces)
        self.subtitle_regex_group.setChecked(custom)

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
        """Whether subtitle cleanup is on (checked or partly checked)."""
        return self.use_subtitle_regex_checkbox.checkState() != Qt.CheckState.Unchecked

    def set_use_subtitle_regex_filter(self, value: bool) -> None:
        """Set the toggle, then re-derive checked / partly checked from the pattern."""
        self.use_subtitle_regex_checkbox.setTristate(False)
        self.use_subtitle_regex_checkbox.setChecked(value)
        self._sync_cleanup_box()

    def get_secondary_subtitle_enabled(self) -> bool:
        return self.secondary_subtitle_checkbox.isChecked()

    def set_secondary_subtitle_enabled(self, value: bool) -> None:
        self.secondary_subtitle_checkbox.setChecked(value)

    # --- Sentence rule (dedup / i+1) and sentence length (C13) ---

    def _sentence_rule_search_text(self) -> tuple[str, ...]:
        """Searchable text for ``sentence_rule_combo``: every item plus its tooltip.

        "dedup" and "deduplicate" are plain English keywords, not translated
        strings: the tooltip only spells out "deduplication".
        """
        parts: list[str] = ["dedup", "deduplicate"]
        combo = self.sentence_rule_combo
        for index in range(combo.count()):
            parts.append(combo.itemText(index))
            tooltip = combo.itemData(index, Qt.ItemDataRole.ToolTipRole)
            if tooltip:
                parts.append(str(tooltip))
        return tuple(parts)

    #: combo item data -> (deduplicate_sentences, use_i_plus_one_filter). i+1
    #: already overrides dedup in EpisodeProcessor, so a source config with
    #: both booleans set selects "i_plus_one" same as one with only i+1 set.
    _SENTENCE_RULE_VALUES: dict[str, tuple[bool, bool]] = {
        "all": (False, False),
        "dedup": (True, False),
        "i_plus_one": (False, True),
    }

    def get_sentence_rule(self) -> tuple[bool, bool]:
        """Return (deduplicate_sentences, use_i_plus_one_filter) for the current selection."""
        return self._SENTENCE_RULE_VALUES[self.sentence_rule_combo.currentData()]

    def set_sentence_rule(self, deduplicate_sentences: bool, use_i_plus_one_filter: bool) -> None:
        """Select the combo item matching the two source booleans."""
        if use_i_plus_one_filter:
            value = "i_plus_one"
        elif deduplicate_sentences:
            value = "dedup"
        else:
            value = "all"
        index = self.sentence_rule_combo.findData(value)
        if index >= 0:
            self.sentence_rule_combo.setCurrentIndex(index)

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

    # --- Full sentences ---

    def get_merge_incomplete_cues(self) -> bool:
        """Return whether incomplete subtitle lines merge into full sentences."""
        return self.merge_incomplete_cues_checkbox.isChecked()

    def set_merge_incomplete_cues(self, value: bool) -> None:
        """Set the full-sentence merge checkbox."""
        self.merge_incomplete_cues_checkbox.setChecked(value)

    # --- Card formatting ---

    def get_bold_target_in_sentence(self) -> bool:
        """Return whether the target word is bolded in the sentence field."""
        return self.bold_target_in_sentence_checkbox.isChecked()

    def set_bold_target_in_sentence(self, value: bool) -> None:
        """Set the bold-target-in-sentence checkbox."""
        self.bold_target_in_sentence_checkbox.setChecked(value)

    # ------------------------------------------------------------------
    # Config marshalling contract (OVH-019)
    # ------------------------------------------------------------------

    def load_from_config(self, config) -> None:
        """Populate all widgets from ``config``.

        Called by :meth:`SettingsTab._load_config` as part of the panel loop.
        """
        default = get_profile(config_language(config)).scoped_defaults.get("subtitle_regex_filter", "")
        self._cleanup_pieces = builtin_cleanup_pieces(str(default or ""))
        self.set_subtitle_regex_filter(config.subtitle_regex_filter)
        self.set_subtitle_regex_replacement(config.subtitle_regex_replacement)
        self.set_use_subtitle_regex_filter(config.use_subtitle_regex_filter)
        self._open_disclosure_for_custom_pattern()
        self.set_secondary_subtitle_enabled(config.secondary_subtitle_enabled)
        self.set_merge_incomplete_cues(config.merge_incomplete_cues)
        self.set_bold_target_in_sentence(config.bold_target_in_sentence)
        self.set_sentence_rule(config.deduplicate_sentences, config.use_i_plus_one_filter)
        self.set_max_sentence_duration_seconds(config.max_sentence_duration_seconds)
        self.set_max_sentence_chars(config.max_sentence_chars)

    def contribute(self, config):
        """Return a new config with this panel's fields applied.

        Uses ``dataclasses.replace`` so the frozen-config invariant is preserved.
        Called by :meth:`SettingsTab.commit_settings` as part of the contribute fold.

        Note: ``subtitle_regex_filter`` and ``use_subtitle_regex_filter`` are
        read here (behind the accessors) but the *validation* of the regex pattern
        stays in :meth:`SettingsTab.commit_settings` — it runs before the fold
        so any invalid pattern aborts Save before ``contribute`` is ever called.
        """
        deduplicate_sentences, use_i_plus_one_filter = self.get_sentence_rule()
        return replace(
            config,
            subtitle_regex_filter=self.get_subtitle_regex_filter(),
            subtitle_regex_replacement=self.get_subtitle_regex_replacement(),
            use_subtitle_regex_filter=self.get_use_subtitle_regex_filter(),
            secondary_subtitle_enabled=self.get_secondary_subtitle_enabled(),
            merge_incomplete_cues=self.get_merge_incomplete_cues(),
            bold_target_in_sentence=self.get_bold_target_in_sentence(),
            deduplicate_sentences=deduplicate_sentences,
            use_i_plus_one_filter=use_i_plus_one_filter,
            max_sentence_duration_seconds=self.get_max_sentence_duration_seconds(),
            max_sentence_chars=self.get_max_sentence_chars(),
        )
