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
from PyQt6.QtGui import QCursor, QDesktopServices
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.resources.styles.theme import Theme, ThemeGroupEntry
from anki_miner.gui.widgets.enhanced import ModernButton

logger = logging.getLogger(__name__)


# Single dial that drives row geometry, glyph pixel size, and button bounding
# box. Bumped from 32 → 36 so the auto-sized star has comfortable headroom.
_ROW_HEIGHT_PX = 36

# Unicode star glyphs. Routed through the font pipeline so hinting/AA stays
# sharp at small sizes — no QPainter math, no devicePixelRatio handling.
_STAR_FILLED = "★"
_STAR_OUTLINE = "☆"

# Color used for the dimmed (partial) family star. Same glyph as filled,
# lower alpha — reads as "some but not all variants favorited".
_FAMILY_STAR_PARTIAL_OPACITY = 0.45


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
    COL_NAME = 0  # tree expander + name
    COL_STATUS = 1  # "Active" marker
    COL_STAR = 2  # favorite toggle

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

        self.tree = QTreeWidget(self)
        # objectName lets common.qss scope styling overrides to just this tree
        # without disturbing other trees in the app.
        self.tree.setObjectName("themesPanelTree")
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Name", "Status", ""])
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.tree.setEditTriggers(QTreeWidget.EditTrigger.NoEditTriggers)
        self.tree.setIndentation(18)

        header = self.tree.header()
        if header is not None:
            header.setSectionResizeMode(self.COL_NAME, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(self.COL_STATUS, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(self.COL_STAR, QHeaderView.ResizeMode.ResizeToContents)
            header.setStretchLastSection(False)

        # Row min-height keeps the star button vertically centered.
        self.tree.setStyleSheet(f"QTreeWidget::item {{ padding: 0; min-height: {_ROW_HEIGHT_PX}px; }}")

        self.tree.itemSelectionChanged.connect(self._on_row_selected)
        layout.addWidget(self.tree)

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
        """Rebuild the tree from the current Theme state."""
        groups = Theme.get_themes_grouped()
        favorites = set(Theme.get_favorites())
        active = Theme.get_current_mode()

        self.tree.blockSignals(True)
        try:
            self.tree.clear()
            for family_name, entries in groups:
                if family_name is None:
                    # Standalone: render the single entry as a top-level row.
                    entry = entries[0]
                    item = QTreeWidgetItem(
                        [
                            entry.display_name,
                            "Active" if entry.key == active else "",
                            "",
                        ]
                    )
                    item.setData(self.COL_NAME, Qt.ItemDataRole.UserRole, entry.key)
                    self.tree.addTopLevelItem(item)
                    star = self._build_star_cell(entry.key, entry.key in favorites)
                    self.tree.setItemWidget(item, self.COL_STAR, star)
                    if entry.key == active:
                        self.tree.setCurrentItem(item)
                else:
                    family_item = QTreeWidgetItem([family_name, "", ""])
                    # Family rows are not selectable; clicking the name only
                    # expands/collapses.
                    family_item.setFlags(family_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                    family_item.setData(self.COL_NAME, Qt.ItemDataRole.UserRole, None)
                    self.tree.addTopLevelItem(family_item)

                    active_inside = False
                    for entry in entries:
                        child = QTreeWidgetItem(
                            [
                                entry.variant_name,
                                "Active" if entry.key == active else "",
                                "",
                            ]
                        )
                        child.setData(self.COL_NAME, Qt.ItemDataRole.UserRole, entry.key)
                        family_item.addChild(child)
                        star = self._build_star_cell(entry.key, entry.key in favorites)
                        self.tree.setItemWidget(child, self.COL_STAR, star)
                        if entry.key == active:
                            self.tree.setCurrentItem(child)
                            active_inside = True

                    family_star = self._build_family_star_cell(family_name, entries, favorites)
                    self.tree.setItemWidget(family_item, self.COL_STAR, family_star)

                    if active_inside:
                        family_item.setExpanded(True)
        finally:
            self.tree.blockSignals(False)

    def _build_star_cell(self, key: str, is_favorite: bool) -> QWidget:
        """Build a centered star-button cell for the given theme row.

        QToolButton sidesteps the global ``QPushButton { padding: 4px 12px }``
        rule that previously crushed icon-only buttons. ``autoRaise=True``
        gives the ghost look (transparent background + hover highlight)
        without an ``objectName`` override. The Unicode glyph routes through
        the font pipeline so it stays sharp without QPainter or
        devicePixelRatio handling.

        Sizing auto-derives from ``_ROW_HEIGHT_PX``: font is 60% of row
        height, button bounding box is the larger of the glyph's line height
        and ``_ROW_HEIGHT_PX - 4``. Change the row height constant and the
        star scales with it — no separate QSS pixel values to keep in sync.

        The QToolButton is wrapped in a QWidget+QHBoxLayout so it sits on the
        row's centerline regardless of cell padding — placing the button
        directly into the cell left it floating at the cell's top-left corner.
        """
        button = QToolButton()
        button.setObjectName("starToggle")
        button.setCheckable(True)
        button.setChecked(is_favorite)
        button.setText(_STAR_FILLED if is_favorite else _STAR_OUTLINE)
        button.setAutoRaise(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip("Click to add to / remove from favorites.")
        # `key` captured per-row via default arg, sidesteps closure-over-loop-var.
        button.clicked.connect(lambda _checked=False, k=key: self._toggle_favorite(k))

        # Button always fits the row (cell padding is zeroed by the scoped
        # QSS rule on `#themesPanelTree`). 1-px margin on each side keeps
        # the button from butting up against the row divider.
        side = _ROW_HEIGHT_PX - 2
        button.setFixedSize(side, side)
        # 60% of row height gives a readable ★ glyph that fits comfortably
        # inside the button. Set via instance stylesheet so the base
        # `QWidget { font-size: 14px }` rule from common.qss can't override
        # it during a style re-polish.
        font_px = int(_ROW_HEIGHT_PX * 0.6)
        button.setStyleSheet(f"font-size: {font_px}px;")

        wrapper = QWidget(self.tree)
        wrapper_layout = QHBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.setSpacing(0)
        wrapper_layout.addWidget(button, 0, Qt.AlignmentFlag.AlignCenter)
        return wrapper

    def _build_family_star_cell(
        self,
        family_name: str,
        entries: list[ThemeGroupEntry],
        favorites: set[str],
    ) -> QWidget:
        """Tri-state favorite star for a family row.

        Visual:
            0 favorited → outline ☆
            all favorited → filled ★
            partial → filled ★ at reduced opacity

        Click rule: if all are favorited, unfavorite all; otherwise favorite all.
        Same fixed height as ``_build_star_cell`` to keep family/variant rows aligned.
        """
        wrapper = QWidget(self)
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        button = QToolButton(wrapper)
        button.setObjectName("starToggle")
        button.setAutoRaise(True)
        button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        side = _ROW_HEIGHT_PX - 2
        button.setFixedSize(side, side)

        keys = [e.key for e in entries]
        favorited_keys = [k for k in keys if k in favorites]
        n_fav = len(favorited_keys)
        n_total = len(keys)
        font_size = int(_ROW_HEIGHT_PX * 0.6)

        if n_fav == 0:
            button.setText(_STAR_OUTLINE)
            tooltip = f"Favorite all {n_total} {family_name} variants."
            button.setStyleSheet(f"font-size: {font_size}px;")
        elif n_fav == n_total:
            button.setText(_STAR_FILLED)
            tooltip = f"Unfavorite all {n_total} {family_name} variants."
            button.setStyleSheet(f"font-size: {font_size}px;")
        else:
            button.setText(_STAR_FILLED)
            tooltip = f"{n_fav} of {n_total} {family_name} variants favorited. " "Click to favorite all."
            alpha = int(_FAMILY_STAR_PARTIAL_OPACITY * 255)
            button.setStyleSheet(f"font-size: {font_size}px; color: rgba(0, 0, 0, {alpha});")

        button.setToolTip(tooltip)
        button.clicked.connect(lambda _checked=False, k=tuple(keys): self._toggle_family_favorites(k))
        layout.addWidget(button)
        return wrapper

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
        item = self.tree.currentItem()
        if item is None:
            return
        # Skip family rows (no key payload — added in Task 5).
        key = item.data(self.COL_NAME, Qt.ItemDataRole.UserRole)
        if not isinstance(key, str):
            return
        if key == Theme.get_current_mode():
            return
        Theme.set_mode(key)
        self._apply_to_app(key)
        # Avoid a full _populate() here — it rebuilt every row (including
        # QPainter-drawn star icons) on each preview click and made theme
        # switching feel laggy. The only visible mutation is the Active marker
        # moving between two rows; update just those.
        self._refresh_active_marker(key)
        self.state_changed.emit(Theme.get_current_mode(), Theme.get_favorites())

    def _refresh_active_marker(self, new_active_key: str) -> None:
        """Move the "Active" Status label to the row matching ``new_active_key``.

        Walks the tree recursively so future nested variant rows (Task 5+) are
        handled without further changes.
        """

        def walk(item: QTreeWidgetItem | None):
            if item is None:
                return
            yield item
            for i in range(item.childCount()):
                yield from walk(item.child(i))

        root = self.tree.invisibleRootItem()
        if root is None:
            return
        for i in range(root.childCount()):
            for descendant in walk(root.child(i)):
                key = descendant.data(self.COL_NAME, Qt.ItemDataRole.UserRole)
                if isinstance(key, str):
                    descendant.setText(self.COL_STATUS, "Active" if key == new_active_key else "")

    def _toggle_favorite(self, key: str) -> None:
        """Star/unstar `key`, refresh the tree, notify listeners."""
        if Theme.is_favorite(key):
            Theme.remove_favorite(key)
        else:
            Theme.add_favorite(key)
        self._populate()
        self.favorites_changed.emit()
        self.state_changed.emit(Theme.get_current_mode(), Theme.get_favorites())

    def _toggle_family_favorites(self, keys: tuple[str, ...]) -> None:
        """Bulk-toggle every variant in a family.

        Rule: if all are favorited, unfavorite all; otherwise favorite all.
        Batches through ``Theme.set_favorites`` so the state listener fires once.
        """
        current = list(Theme.get_favorites())
        current_set = set(current)
        key_set = set(keys)
        all_favorited = key_set.issubset(current_set)
        if all_favorited:
            new_favorites = [k for k in current if k not in key_set]
        else:
            new_favorites = list(current)
            for k in keys:
                if k not in current_set:
                    new_favorites.append(k)
        Theme.set_favorites(new_favorites)
        self._populate()
        self.state_changed.emit(Theme.get_current_mode(), Theme.get_favorites())
        self.favorites_changed.emit()

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
