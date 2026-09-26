"""Checklist of resource-bundle items, grouped by family (used for export and import).

Group headers are plain labels, not tri-state boxes: a row the user cannot
take (already installed, no source file kept) stays visible with its reason
but holds no check state at all, so no parent toggle can ever tick it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.resources.styles import SPACING
from anki_miner.services.resource_bundle import BundleItem

_FAMILY_OF = {
    "dictionary": "dictionary",
    "frequency": "frequency",
    "pitch": "pitch",
    "known_words": "known_words",
    "blacklist": "wordlists",
    "whitelist": "wordlists",
}


@dataclass(frozen=True)
class BundleChoice:
    item: BundleItem
    #: Translated size or word count shown after the name.
    detail: str = ""
    #: Translated reason the row cannot be taken; empty means selectable.
    disabled_reason: str = ""


class ResourceBundleDialog(QDialog):
    """Pick which bundle items to export or install. All selectable rows start checked."""

    def __init__(
        self,
        choices: Sequence[BundleChoice],
        *,
        title: str,
        intro: str,
        accept_text: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(520, 440)
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACING.sm)

        intro_label = QLabel(intro)
        intro_label.setObjectName("helper-text")
        intro_label.setWordWrap(True)
        layout.addWidget(intro_label)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        layout.addWidget(self.tree, 1)

        self._rows: list[tuple[QTreeWidgetItem, BundleItem]] = []
        groups: dict[str, QTreeWidgetItem] = {}
        for choice in choices:
            family = _FAMILY_OF[choice.item.kind]
            group = groups.get(family)
            if group is None:
                group = QTreeWidgetItem(self.tree, [self._family_label(family)])
                group.setFlags(Qt.ItemFlag.ItemIsEnabled)
                groups[family] = group
            text = self._row_label(choice.item)
            if choice.detail:
                text = f"{text}  ({choice.detail})"
            row = QTreeWidgetItem(group, [text])
            if choice.disabled_reason:
                row.setText(0, f"{text} — {choice.disabled_reason}")
                row.setToolTip(0, choice.disabled_reason)
                row.setFlags(Qt.ItemFlag.NoItemFlags)
            else:
                row.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
                row.setCheckState(0, Qt.CheckState.Checked)
                self._rows.append((row, choice.item))
        self.tree.expandAll()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.accept_button = QPushButton(accept_text)
        buttons.addButton(self.accept_button, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.tree.itemChanged.connect(lambda *_: self._refresh_accept())
        self._refresh_accept()

    def selected_items(self) -> list[BundleItem]:
        return [item for row, item in self._rows if row.checkState(0) == Qt.CheckState.Checked]

    def _refresh_accept(self) -> None:
        self.accept_button.setEnabled(bool(self.selected_items()))

    def _family_label(self, family: str) -> str:
        return {
            "dictionary": self.tr("Dictionaries"),
            "frequency": self.tr("Frequency lists"),
            "pitch": self.tr("Pitch accent"),
            "known_words": self.tr("Known words"),
            "wordlists": self.tr("Word lists"),
        }[family]

    def _row_label(self, item: BundleItem) -> str:
        if item.kind == "known_words":
            return self.tr("Known-words ignore list")
        if item.kind == "blacklist":
            return self.tr("Blacklist")
        if item.kind == "whitelist":
            return self.tr("Whitelist")
        return item.name
