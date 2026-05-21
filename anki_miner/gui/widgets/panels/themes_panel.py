"""Themes settings panel.

Lists every discovered theme (shipped + user-installed) and provides:

* Live preview when a row is selected — the active theme actually changes so
  the user sees buttons, tables, scrollbars, banners react in real time.
* A star toggle to add/remove the theme from the favorites list that drives
  the top-right header combo and the Ctrl+T cycle rotation.
* An "Open themes folder" button that surfaces ``~/.anki_miner/themes/`` so
  community-contributed JSON files can be installed by drop-in (see
  discussion #27).
* A "Revert" button that snaps back to whatever was active when the user
  opened the panel — preview safety without a separate Apply/Cancel button.

Persistence is handled by emitting ``state_changed`` (re-uses the
``config_changed`` convention from other panels). The settings tab forwards
to ``MainWindow.update_config`` which writes ``gui_config.json``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.resources.styles.theme import SOURCE_USER, Theme
from anki_miner.gui.widgets.enhanced import ModernButton

logger = logging.getLogger(__name__)


STAR_ON = "★"
STAR_OFF = "☆"


class ThemesPanel(QWidget):
    """Settings panel for managing themes and the favorites rotation.

    Signals:
        state_changed: Emitted with ``(active_theme, favorites_tuple)`` after
            any change the user makes. The settings tab persists by mutating
            the config and saving.
        favorites_changed: Emitted whenever favorites change so the header
            combo can refresh without an extra config round-trip.
    """

    state_changed = pyqtSignal(str, tuple)
    favorites_changed = pyqtSignal()

    # Column indices for clarity.
    COL_STAR = 0
    COL_NAME = 1
    COL_SOURCE = 2
    COL_STATUS = 3

    def __init__(self, themes_root: Path, parent: QWidget | None = None) -> None:
        """Initialize the panel.

        Args:
            themes_root: The user themes directory. Used by the "Open themes
                folder" action; created on demand if missing.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._themes_root = themes_root
        self._preview_baseline: str | None = None

        self._setup_ui()
        self._populate()

    # ---- UI construction -------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout()
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)
        layout.setSpacing(SPACING.sm)

        intro = QLabel(
            "Star themes to add them to the top-right selector. Click any row to preview — "
            "the change applies live across the app. Press <b>Revert</b> to undo your preview."
        )
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(intro)

        self.table = QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels(["", "Name", "Source", "Status"])
        v_header = self.table.verticalHeader()
        if v_header is not None:
            v_header.setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)

        header = self.table.horizontalHeader()
        if header is not None:
            header.setSectionResizeMode(self.COL_STAR, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(self.COL_NAME, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(self.COL_SOURCE, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(self.COL_STATUS, QHeaderView.ResizeMode.ResizeToContents)

        self.table.itemSelectionChanged.connect(self._on_row_selected)
        layout.addWidget(self.table)

        buttons = QHBoxLayout()
        buttons.setSpacing(SPACING.sm)

        self.open_folder_btn = ModernButton("Open themes folder", variant="secondary")
        self.open_folder_btn.setToolTip(
            f"Open {self._themes_root} in your file manager. "
            "Drop Anki Miner theme JSON files here to install them; they appear here on next launch."
        )
        self.open_folder_btn.clicked.connect(self._open_themes_folder)
        buttons.addWidget(self.open_folder_btn)

        self.revert_btn = ModernButton("Revert", variant="secondary")
        self.revert_btn.setToolTip("Restore the theme that was active when this tab was opened.")
        self.revert_btn.clicked.connect(self._revert_preview)
        buttons.addWidget(self.revert_btn)

        buttons.addStretch()
        layout.addLayout(buttons)

        self.setLayout(layout)

    # ---- Population ------------------------------------------------------

    def _populate(self) -> None:
        """Rebuild the table from the current Theme state."""
        available = Theme.get_available_themes()
        favorites = set(Theme.get_favorites())
        active = Theme.get_current_mode()

        self.table.blockSignals(True)
        try:
            self.table.setRowCount(0)
            for row, (key, display) in enumerate(available.items()):
                self.table.insertRow(row)

                # Column 0: star toggle (QPushButton as cell widget so it
                # doesn't trigger row selection when clicked).
                star_btn = QPushButton(STAR_ON if key in favorites else STAR_OFF)
                star_btn.setFlat(True)
                star_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                star_btn.setToolTip("Click to add to / remove from favorites.")
                # `key` is captured per-row; the bound default avoids
                # closure-over-loop-variable bugs.
                star_btn.clicked.connect(lambda _checked=False, k=key: self._toggle_favorite(k))
                self.table.setCellWidget(row, self.COL_STAR, star_btn)
                # Stash the theme key on the row's name item so row-select can
                # find it without an extra dict lookup.
                name_item = QTableWidgetItem(display)
                name_item.setData(Qt.ItemDataRole.UserRole, key)
                self.table.setItem(row, self.COL_NAME, name_item)

                source = Theme.get_theme_source(key) or ""
                source_label = "User" if source == SOURCE_USER else "Shipped"
                source_item = QTableWidgetItem(source_label)
                self.table.setItem(row, self.COL_SOURCE, source_item)

                status_item = QTableWidgetItem("Active" if key == active else "")
                self.table.setItem(row, self.COL_STATUS, status_item)

                if key == active:
                    self.table.selectRow(row)
        finally:
            self.table.blockSignals(False)

    # ---- Events ----------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802 — Qt override
        """Capture the active theme on first show so Revert is meaningful."""
        if self._preview_baseline is None:
            self._preview_baseline = Theme.get_current_mode()
        super().showEvent(event)

    def reset_baseline(self) -> None:
        """Re-capture the active theme as the new revert target.

        Called by the settings tab when the user navigates away from the
        Themes sub-tab so a future visit reverts to whatever they last left
        active, not to the value from session start.
        """
        self._preview_baseline = Theme.get_current_mode()

    # ---- Interactions ----------------------------------------------------

    def _on_row_selected(self) -> None:
        """Live-preview the selected theme."""
        item = self.table.currentItem()
        if item is None:
            return
        row = item.row()
        name_item = self.table.item(row, self.COL_NAME)
        if name_item is None:
            return
        key = name_item.data(Qt.ItemDataRole.UserRole)
        if not key or key == Theme.get_current_mode():
            return
        Theme.set_mode(key)
        self._apply_to_app(key)
        # Status column needs an update on the previously-active row too;
        # cheapest correct path is a full repopulate.
        self._populate()
        self.state_changed.emit(Theme.get_current_mode(), Theme.get_favorites())

    def _toggle_favorite(self, key: str) -> None:
        """Star/unstar `key`, refresh the table, notify listeners."""
        if Theme.is_favorite(key):
            Theme.remove_favorite(key)
        else:
            Theme.add_favorite(key)
        self._populate()
        self.favorites_changed.emit()
        self.state_changed.emit(Theme.get_current_mode(), Theme.get_favorites())

    def _open_themes_folder(self) -> None:
        """Open (creating if necessary) the user themes directory."""
        try:
            self._themes_root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("Could not create themes dir %s: %s", self._themes_root, e)
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._themes_root)))

    def _revert_preview(self) -> None:
        """Restore the theme that was active when the panel was opened."""
        if self._preview_baseline is None or self._preview_baseline == Theme.get_current_mode():
            return
        target = self._preview_baseline
        Theme.set_mode(target)
        self._apply_to_app(target)
        self._populate()
        self.state_changed.emit(Theme.get_current_mode(), Theme.get_favorites())

    def _apply_to_app(self, mode: str) -> None:
        """Repaint the application with the given theme key."""
        app = QApplication.instance()
        if isinstance(app, QApplication):
            Theme.apply_to_app(app, mode)
