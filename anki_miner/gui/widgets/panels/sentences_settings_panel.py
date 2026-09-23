"""Sentence-content settings panel.

Moved off Filtering (now "Word Filters"): these rows shape what the example
sentence itself looks like -- text cleanup, a second subtitle track, whole
sentences instead of fragments, and bolding the mined word -- rather than
which words get mined. "Colour the reading by tone" stays on Filtering for
now; a later task moves it here too.
"""

from __future__ import annotations

from dataclasses import replace

from PyQt6.QtWidgets import QCheckBox, QHBoxLayout, QLineEdit, QPushButton, QWidget

from anki_miner.gui.widgets.base import FormPanel

#: Built-in regex presets for common subtitle noise. Buttons append these to the
#: user's pattern with `|` so multiple presets can be stacked. Patterns target
#: both half-width and full-width punctuation common in JP subtitle files.
SUBTITLE_REGEX_PRESETS: tuple[tuple[str, str], ...] = (
    ("Parens (Tanaka)", r"\([^)]*\)|（[^）]*）"),
    ("Brackets [SFX]", r"\[[^\]]*\]|［[^］]*］"),
    ("Music ♪♬", r"[♪♬♫#～〜]+"),
    ("Speaker: prefix", r"^[^「『:：]+[:：]\s*"),
    # A dash that opens a speaker turn ("- Hi. - Hello."): at the line start or after
    # a sentence terminator. Hyphenated words and a mid-sentence dash are untouched.
    ("Dialogue dash", r"(?:^|(?<=[.!?…]\s))[-–—]\s+"),
)


class SentencesSettingsPanel(FormPanel):
    """Panel for example-sentence content settings.

    Provides:
    - Subtitle text cleanup (regex filter + replacement, with presets)
    - Secondary-language subtitles (F7)
    - Whole-sentence merging across subtitle lines
    - Bolding the mined word in the sentence
    """

    ANCHOR_NAMESPACE = "sentences"

    def __init__(self, parent=None):
        """Initialize the sentences settings panel."""
        super().__init__(self.tr("Sentences"), parent=parent)
        self._setup_fields()

    def _setup_fields(self) -> None:
        """Set up the panel fields."""
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
            self.tr("Dialogue dash"),
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
        # here; "Colour the reading by tone" stays on Filtering until a later
        # task moves it too.
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
        self.set_subtitle_regex_filter(config.subtitle_regex_filter)
        self.set_subtitle_regex_replacement(config.subtitle_regex_replacement)
        self.set_use_subtitle_regex_filter(config.use_subtitle_regex_filter)
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
