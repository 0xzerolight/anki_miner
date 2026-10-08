"""Settings -> Utilities: which tools the Utilities tab shows.

One checkbox per tool, in tab order; checked = shown. A toggle commits at once
through ``hidden_utilities_changed`` (SettingsTab persists it), like the
Keyboard page, so this is not one of the Save-path panels. The last visible
checked box is disabled, so the tab always keeps a tool.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QLabel, QWidget

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.capabilities import (
    effective_hidden_utilities,
    hidden_utilities_on_tab,
    language_gated_utilities,
    utility_labels,
)
from anki_miner.gui.widgets.base import FormPanel
from anki_miner.languages.registry import config_language, get_profile


class UtilitiesSettingsPanel(FormPanel):
    """One checkbox per Utilities tool.

    Signals:
        hidden_utilities_changed: Emitted with the tuple of hidden Utilities
            tool keys, in tab order, after the user toggles a tool.
    """

    ANCHOR_NAMESPACE = "utilities"

    hidden_utilities_changed = pyqtSignal(tuple)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build one box per tool, all checked until a config is loaded."""
        super().__init__(self.tr("Utilities"), parent=parent)
        self.utility_checkboxes: dict[str, QCheckBox] = {}
        self._setup_fields()

    def _setup_fields(self) -> None:
        helper = QLabel(self.tr("Choose which tools the Utilities tab shows. At least one stays."))
        helper.setObjectName("helper-text")
        helper.setWordWrap(True)
        self.add_widget(helper)
        for key, label in utility_labels().items():
            box = QCheckBox(label)
            box.setChecked(True)
            box.toggled.connect(self._on_utility_toggled)
            self.utility_checkboxes[key] = box
            # Loop-built, so the anchor name is explicit ("utilities.retime").
            self.add_field("", box, anchor=key)
        self.add_stretch()

    def _on_utility_toggled(self, _checked: bool) -> None:
        """Persist which Utilities tools are hidden (applies at once)."""
        self._sync_utility_lock()
        self.hidden_utilities_changed.emit(
            tuple(key for key, box in self.utility_checkboxes.items() if not box.isChecked())
        )

    def _sync_utility_lock(self) -> None:
        """Disable the only checked box, so the Utilities tab always keeps a tool.

        Counts only the boxes on screen: a language-gated box (Manga OCR outside
        Japanese) stays checked but is no tool the user can see (E17).
        """
        checked = sum(box.isChecked() for box in self.utility_checkboxes.values() if not box.isHidden())
        for box in self.utility_checkboxes.values():
            box.setEnabled(checked > 1 or not box.isChecked())

    def load_from_config(self, config: AnkiMinerConfig) -> None:
        """Repaint every box from ``config`` without emitting (SettingsTab._load_config)."""
        # The rule SubtitlesTab applies, so the boxes show what the tab shows:
        # unknown keys ignored, and when the user's hidden tools plus the
        # language-gated ones would hide everything, every usable tool is shown.
        capabilities = get_profile(config_language(config)).capabilities
        gated = language_gated_utilities(capabilities)
        on_tab = hidden_utilities_on_tab(config.hidden_utilities, capabilities)
        stored = effective_hidden_utilities(config.hidden_utilities)
        for key, box in self.utility_checkboxes.items():
            box.blockSignals(True)
            try:
                # A gated box (E17: Manga OCR outside Japanese) is off screen and
                # keeps the user's own choice: the gate never writes
                # hidden_utilities, so a switch back finds it unchanged.
                box.setChecked(key not in (stored if key in gated else on_tab))
            finally:
                box.blockSignals(False)
            # Before the lock, which counts only the boxes the user can see (P1).
            box.setHidden(key in gated)
        self._sync_utility_lock()
