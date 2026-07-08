"""Structural preview of the manga volume(s) a folder resolves to.

The manga tab's *Preview* button classifies the selected folder with
``detector.detect`` and shows the resulting refs here — a single volume is one
row, a series folder is N rows. This is informational only (Close to dismiss),
mirroring :class:`~anki_miner.gui.widgets.dialogs.pair_preview_dialog.PairPreviewDialog`:
it lists *what would be mined* without tokenizing anything. Actual words are
inspected during Mine via the "Review words before mining" curation popup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtGui import QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.qt_helpers import add_min_max_buttons
from anki_miner.gui.widgets.enhanced import ModernButton, SectionHeader
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from anki_miner.services.reading.models import ReadingSourceRef

# Human-readable label per ref kind, shown in the Format column.
_KIND_LABELS = {
    "mokuro": "Mokuro",
    "epub": "EPUB",
    "txt": "Text",
}


class MangaVolumesPreviewDialog(QDialog):
    """Informational list of the volumes a manga folder resolves to.

    Shows one row per :class:`ReadingSourceRef` (title, volume, format, source
    path). Close-only — no Mine button; the caller starts mining from the tab.
    """

    def __init__(self, refs: list[ReadingSourceRef], parent: QWidget | None = None) -> None:
        """Initialize the dialog.

        Args:
            refs: Detected reading-source refs (already classified by
                ``detector.detect``; never loaded here).
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.refs = refs
        self._setup_ui()
        add_min_max_buttons(self)

    def _setup_ui(self) -> None:
        """Build the header, volume table, and Close footer."""
        self.setWindowTitle(tr_format(self.tr("Preview Volumes — %1 found"), len(self.refs)))
        self.setMinimumSize(720, 480)
        self.resize(820, 540)

        main_layout = QVBoxLayout()
        main_layout.setSpacing(SPACING.md)
        main_layout.setContentsMargins(SPACING.lg, SPACING.lg, SPACING.lg, SPACING.lg)

        main_layout.addWidget(
            SectionHeader(
                tr_format(self.tr("Volume Preview: %1 volume(s)"), len(self.refs)),
            )
        )

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(
            [self.tr("Title"), self.tr("Volume"), self.tr("Format"), self.tr("Source")]
        )
        self.table.setRowCount(len(self.refs))
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)

        h_header = self.table.horizontalHeader()
        if h_header:
            h_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            h_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            h_header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
            h_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        v_header = self.table.verticalHeader()
        if v_header:
            v_header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)

        # Suspend repaints during populate (parity with PairPreviewDialog).
        self.table.setUpdatesEnabled(False)
        try:
            for row, ref in enumerate(self.refs):
                title_item = QTableWidgetItem(ref.title or "")
                self.table.setItem(row, 0, title_item)

                self.table.setItem(row, 1, QTableWidgetItem(ref.volume or ""))

                kind_label = _KIND_LABELS.get(ref.kind, ref.kind)
                self.table.setItem(row, 2, QTableWidgetItem(kind_label))

                source_item = QTableWidgetItem(ref.path.name)
                source_item.setToolTip(str(ref.path))
                self.table.setItem(row, 3, source_item)
        finally:
            self.table.setUpdatesEnabled(True)

        main_layout.addWidget(self.table)

        footer_layout = QHBoxLayout()
        footer_layout.setSpacing(SPACING.sm)
        info_label = QLabel(self.tr("Volumes mine in order. No cards are created by Preview."))
        info_font = QFont()
        info_font.setPixelSize(12)
        info_label.setFont(info_font)
        footer_layout.addWidget(info_label)
        footer_layout.addStretch()

        close_button = ModernButton(self.tr("Close"), variant="primary")
        close_button.clicked.connect(self.accept)
        close_button.setMinimumWidth(120)
        footer_layout.addWidget(close_button)

        main_layout.addLayout(footer_layout)
        self.setLayout(main_layout)

        escape_shortcut = QShortcut(QKeySequence("Esc"), self)
        escape_shortcut.activated.connect(self.reject)
