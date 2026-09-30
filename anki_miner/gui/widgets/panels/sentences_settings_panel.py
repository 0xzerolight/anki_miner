"""Sentence-content settings panel.

Moved off Filtering (now "Word Filters"): these rows shape what the example
sentence itself looks like -- text cleanup, a second subtitle track, whole
sentences instead of fragments, and bolding the mined word -- rather than
which words get mined. "Colour the reading by tone" moved off Filtering too
(T10), onto Cards & Anki, not here -- it colours a card field, not a sentence.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QCheckBox, QFormLayout, QGroupBox, QLineEdit, QVBoxLayout, QWidget

from anki_miner.gui.widgets.base import FormPanel
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
                "Removes (notes), [sound effects], ♪ music, speaker labels and dialogue dashes from each "
                "subtitle line before mining. Half-checked means your own pattern is in use: click to add "
                "every built-in cleanup to it."
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

        # Checkable group used as a disclosure, like Cards & Anki's marker
        # names: Qt's checkable group only disables its children, so the body
        # is hidden and shown instead. Opens by itself on load when a custom
        # pattern is stored (see _open_disclosure_for_custom_pattern).
        self.subtitle_regex_group = QGroupBox(self.tr("Edit the pattern (advanced)"))
        self.subtitle_regex_group.setCheckable(True)
        self.subtitle_regex_group.setChecked(False)
        group_layout = QVBoxLayout(self.subtitle_regex_group)
        self._subtitle_regex_body = QWidget()
        body_form = QFormLayout(self._subtitle_regex_body)
        body_form.setContentsMargins(0, 0, 0, 0)
        body_form.addRow(self.tr("Regex Filter:"), self.subtitle_regex_edit)
        body_form.addRow(self.tr("Replacement:"), self.subtitle_replacement_edit)
        group_layout.addWidget(self._subtitle_regex_body)
        self._subtitle_regex_body.setVisible(False)
        self.subtitle_regex_group.toggled.connect(self._subtitle_regex_body.setVisible)
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

        # Secondary Subtitles (F7). One toggle gates every new surface.
        self.add_section(self.tr("Secondary Subtitles"))
        self.secondary_subtitle_checkbox = QCheckBox(self.tr("Enable secondary-language subtitles"))
        self.add_field(
            "",
            self.secondary_subtitle_checkbox,
            helper=self.tr(
                "Adds a second subtitle picker and its own offset to Video -> Single. Its line shows under "
                "the mining-language line in the Word Curator preview and, when the Translation field is "
                "mapped (Cards & Anki), on the card."
            ),
        )

        # Full Sentences section (FUTURE_IDEAS 6).
        self.add_section(self.tr("Full Sentences"))

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

        # Card Formatting section (Issue #20). Only the bold-target row lives
        # here; "Colour the reading by tone" moved to Cards & Anki instead
        # (T10) -- it colours a card field, not a sentence.
        self.add_section(self.tr("Card Formatting"))

        self.bold_target_in_sentence_checkbox = QCheckBox(self.tr("Bold target word in sentence"))
        # QToolTip has no PlainText format and auto-detects HTML when the
        # string contains tag-like substrings. Escape the angle brackets so
        # the literal "<b>...</b>" markup is visible. Issue #20.
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
            subtitle_regex_filter=self.get_subtitle_regex_filter(),
            subtitle_regex_replacement=self.get_subtitle_regex_replacement(),
            use_subtitle_regex_filter=self.get_use_subtitle_regex_filter(),
            secondary_subtitle_enabled=self.get_secondary_subtitle_enabled(),
            merge_incomplete_cues=self.get_merge_incomplete_cues(),
            bold_target_in_sentence=self.get_bold_target_in_sentence(),
        )
