"""First visit to a mining language: tick the decks the known-words scan must skip (spec S15).

A script gate cannot tell languages that share a script apart — a Latin gate
reads every English, French or Spanish card as known — so the choice has to be
the user's, once, on the first switch. Every deck is listed and ticked except
the language's own deck; the ticked names become that language's scoped
``excluded_decks``. Strings keep the ``LanguageSwitch`` context the prompt this
replaces used, so its existing translations still apply.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

from PyQt6.QtCore import QCoreApplication, Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.resources.styles import SPACING
from anki_miner.utils.i18n import tr_format

CHOICE_EXCLUDE = "exclude"
CHOICE_SETUP = "setup"
CHOICE_NONE = "none"


class FirstVisitDecksDialog(QDialog):
    """Checklist of decks; Exclude ticked decks / Set up resources… / Close."""

    def __init__(
        self,
        parent: QWidget | None,
        *,
        display_name: str,
        decks: Sequence[str],
        ticked: Collection[str],
        offer_setup: bool,
    ) -> None:
        super().__init__(parent)
        self.choice = CHOICE_NONE
        self.setWindowTitle(QCoreApplication.translate("LanguageSwitch", "First time mining this language"))
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACING.sm)
        layout.addWidget(
            QLabel(
                tr_format(QCoreApplication.translate("LanguageSwitch", "You have not mined %1 before."), display_name)
            )
        )
        explanation = QLabel(
            tr_format(
                QCoreApplication.translate(
                    "LanguageSwitch",
                    "The known-words scan reads every deck that is not excluded, and it cannot tell apart "
                    "languages that share a script: words in a ticked deck would not count as known in %1. "
                    "Untick the decks that hold %1 cards.",
                ),
                display_name,
            )
        )
        explanation.setWordWrap(True)
        explanation.setObjectName("helper-text")
        layout.addWidget(explanation)

        self.deck_list = QListWidget()
        for name in decks:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if name in ticked else Qt.CheckState.Unchecked)
            self.deck_list.addItem(item)
        layout.addWidget(self.deck_list)

        buttons = QHBoxLayout()
        self.exclude_button = QPushButton(QCoreApplication.translate("LanguageSwitch", "Exclude ticked decks"))
        self.exclude_button.setDefault(True)
        self.exclude_button.clicked.connect(lambda: self._finish(CHOICE_EXCLUDE))
        buttons.addWidget(self.exclude_button)
        self.setup_button: QPushButton | None = None
        if offer_setup:
            self.setup_button = QPushButton(QCoreApplication.translate("LanguageSwitch", "Set up resources…"))
            self.setup_button.clicked.connect(lambda: self._finish(CHOICE_SETUP))
            buttons.addWidget(self.setup_button)
        buttons.addStretch()
        close = QPushButton(QCoreApplication.translate("LanguageSwitch", "Close"))
        close.clicked.connect(self.reject)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def _finish(self, choice: str) -> None:
        self.choice = choice
        self.accept()

    def ticked_decks(self) -> tuple[str, ...]:
        items = (self.deck_list.item(row) for row in range(self.deck_list.count()))
        return tuple(item.text() for item in items if item is not None and item.checkState() == Qt.CheckState.Checked)
