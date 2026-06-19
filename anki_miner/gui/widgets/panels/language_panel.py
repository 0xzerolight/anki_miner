"""Language settings panel (Discussion #76).

A simple language picker. The choice persists immediately (live-persist, like
ThemesPanel) but applies on next launch — Qt widgets capture their tr() strings
at construction, and we deliberately do NOT implement live retranslateUi.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from anki_miner.gui.i18n import available_languages
from anki_miner.gui.resources.styles import SPACING


class LanguagePanel(QWidget):
    """Settings panel for choosing the UI language.

    Signals:
        language_changed: Emitted with the selected language code when the user
            picks a new language (not on programmatic ``set_language``).
    """

    language_changed = pyqtSignal(str)

    def __init__(self, current_language: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._setup_ui()
        self.set_language(current_language)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout()
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)
        layout.setSpacing(SPACING.sm)

        intro = QLabel(self.tr("Choose the language for the app interface."))
        intro.setWordWrap(True)
        layout.addWidget(intro)

        row = QHBoxLayout()
        row.setSpacing(SPACING.sm)
        row.addWidget(QLabel(self.tr("Language")))

        self.language_combo = QComboBox()
        self.language_combo.setObjectName("languageCombo")
        for code, name in available_languages().items():
            self.language_combo.addItem(name, code)
        # `activated` fires only on user interaction (not on programmatic
        # setCurrentIndex in set_language).
        self.language_combo.activated.connect(self._on_language_selected)
        row.addWidget(self.language_combo)
        row.addStretch(1)
        layout.addLayout(row)

        # Hidden until a change is made; restart-to-apply hint.
        self.restart_note = QLabel(self.tr("Restart Anki Miner to apply the new language."))
        self.restart_note.setWordWrap(True)
        self.restart_note.setVisible(False)
        layout.addWidget(self.restart_note)

        layout.addStretch(1)
        self.setLayout(layout)

    def set_language(self, code: str) -> None:
        """Select ``code`` in the combo without emitting (external sync)."""
        idx = self.language_combo.findData(code, Qt.ItemDataRole.UserRole)
        if idx < 0:
            idx = self.language_combo.findData("en", Qt.ItemDataRole.UserRole)
        self.language_combo.blockSignals(True)
        try:
            self.language_combo.setCurrentIndex(max(0, idx))
        finally:
            self.language_combo.blockSignals(False)

    def _on_language_selected(self, index: int) -> None:
        code = self.language_combo.itemData(index)
        if not isinstance(code, str):
            return
        self.restart_note.setVisible(True)
        self.language_changed.emit(code)
