"""UI settings panel — language, zoom, text size, and theme selection.

This is the "UI" Settings sub-tab. Top to bottom it offers:

* UI language picker (restart-to-apply; merged in from the former
  ``LanguagePanel``). Emits ``language_changed``.
* Zoom (whole-UI scale) and Text size, both restart-to-apply (D39b-A). Text size
  commits instantly and offers *Restart now* / *Later*; changing it relayouts the
  whole window, so unlike theme there is no instant path to have.
* The theme list (shipped + user-installed) with:
  - Live preview when a row is selected — the active theme actually changes so
    the user sees buttons, tables, scrollbars, banners react in real time.
  - A star toggle to add/remove the theme from the favorites list that drives
    the top-right header combo and the Ctrl+T cycle rotation.
  - An "Open themes folder" button that surfaces ``~/.anki_miner/themes/`` so
    community-contributed JSON files can be installed by drop-in (see
    discussion #27).
  - A "Revert" button that snaps back to whatever was active when the user
    opened the panel — preview safety without a separate Apply/Cancel button.
  - A contrast note under the tree, stating the measured ratio when the live
    theme is hard to read. Advisory only: the theme still renders exactly as
    its author wrote it (D43-A).

Persistence is handled by emitting ``state_changed`` / ``font_scale_changed`` /
``zoom_changed`` / ``language_changed`` (re-uses the ``config_changed``
convention from other panels). The settings tab forwards to
``MainWindow.update_config`` which writes ``gui_config.json``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QCursor, QDesktopServices
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui import restart
from anki_miner.gui.i18n import available_languages
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.resources.styles.theme import (
    CONTRAST_ROLE_MUTED_TEXT,
    CONTRAST_ROLE_PRIMARY_LABEL,
    CONTRAST_ROLE_SURFACE_EDGE,
    ContrastIssue,
    Theme,
    ThemeGroupEntry,
    assess_theme_contrast,
)
from anki_miner.gui.widgets.base import ScreenIssue, ScreenIssueHost, SettingAnchorHost
from anki_miner.gui.widgets.enhanced import ModernButton
from anki_miner.utils.i18n import tr_format

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

# Discrete UI font-scale presets (whole percents) offered in the Text size
# dropdown. All values sit inside the [0.5, 2.0] clamp range. A dropdown is
# used instead of a slider because QComboBox is styled (common.qss) and clearly
# visible, whereas the bare QSlider had no QSS and rendered near-invisible
# (Issue #63).
FONT_SCALE_PRESETS = (50, 75, 100, 125, 150, 175, 200)

# Discrete whole-UI zoom presets (whole percents) offered in the Zoom dropdown.
# All values sit inside the [0.5, 2.0] clamp range. Unlike Text size, zoom is
# restart-to-apply (injected as QT_SCALE_FACTOR before QApplication is built),
# so there is no live preview — only a restart note. 50% is omitted because a
# half-size whole UI is cramped to the point of unusable; the font-only Text
# size still goes down to 50% for users who only need smaller text.
ZOOM_PRESETS = (75, 100, 125, 150, 175, 200)


class UISettingsPanel(ScreenIssueHost, SettingAnchorHost, QWidget):
    """Settings panel for UI language, zoom, text size, and theme selection.

    Signals:
        state_changed: Emitted with ``(active_theme, favorites_tuple)`` after
            any change the user makes. The settings tab persists by mutating
            the config and saving.
        favorites_changed: Emitted whenever favorites change so the header
            combo can refresh without an extra config round-trip.
        font_scale_changed: Emitted with the new UI font scale (Text size).
        zoom_changed: Emitted with the new whole-UI zoom factor.
        language_changed: Emitted with the selected language code when the user
            picks a new UI language (not on programmatic ``set_language``).
        manage_profiles_requested: Emitted when the user asks for the settings
            profile manager. The panel deliberately does not own the dialog —
            switching a profile reloads this very panel.
    """

    ANCHOR_NAMESPACE = "ui"

    state_changed = pyqtSignal(str, tuple)
    favorites_changed = pyqtSignal()
    font_scale_changed = pyqtSignal(float)
    zoom_changed = pyqtSignal(float)
    language_changed = pyqtSignal(str)
    native_dialogs_changed = pyqtSignal(bool)
    manage_profiles_requested = pyqtSignal()

    # Column indices for clarity.
    COL_NAME = 0  # tree expander + name
    COL_STATUS = 1  # "Active" marker
    COL_STAR = 2  # favorite toggle

    def __init__(
        self,
        themes_root: Path,
        ui_zoom: float = 1.0,
        ui_language: str = "en",
        use_native_file_dialogs: bool = False,
        ui_font_scale: float = 1.0,
        parent: QWidget | None = None,
    ) -> None:
        """Initialize the panel.

        Args:
            themes_root: The user themes directory. Used by the "Open themes
                folder" action; created on demand if missing.
            ui_zoom: The persisted whole-UI zoom factor, used to seed the Zoom
                dropdown. Zoom is restart-to-apply (QT_SCALE_FACTOR), so there
                is no live Theme state to read it from — it is passed in.
            ui_language: The persisted UI language code, used to seed the
                Language dropdown. Restart-to-apply, so it is passed in.
            use_native_file_dialogs: Seeds the "Use system file dialogs"
                checkbox (Issue #100 — non-native Qt dialogs are the default).
            ui_font_scale: The persisted UI font scale, used to seed the Text
                size dropdown. Restart-to-apply (D39b-A), so the *pending*
                config value is what the combo shows — never the running
                ``Theme.get_font_scale()``, which stays on the boot value for
                the life of the process.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._themes_root = themes_root
        self._ui_zoom = ui_zoom
        self._ui_font_scale = ui_font_scale
        self._use_native_file_dialogs = use_native_file_dialogs
        # Construction-time values = what Qt is actually running with: the panel
        # is built once at app boot from the boot config, and language, zoom and
        # text size only take effect at startup. ``load_from_config`` compares
        # against these so an A → B → A round trip clears the restart note again
        # instead of latching it on for the rest of the session.
        self._boot_language = ui_language
        self._boot_zoom = ui_zoom
        # Read from Theme, not from the argument: this is what the running
        # process was actually styled with, which is the only honest baseline
        # for "will change after restart".
        self._boot_font_scale = Theme.get_font_scale()
        # `Later` hides the note for the session without touching the persisted
        # value; a fresh selection reveals it again.
        self._font_scale_note_dismissed = False
        self._preview_baseline: str | None = None
        # The theme this panel last *saw*: the previous load's ``config.theme``,
        # or whatever the panel itself made live since. ``load_from_config``
        # compares against it to tell a genuine external theme change (profile
        # switch, Import Settings, the header combo) apart from the panel's own
        # live preview — the preview is exactly what Revert exists to undo, so a
        # reload triggered by some unrelated field must not re-point the revert
        # baseline at the previewed theme. ``None`` until the first load.
        self._last_seen_theme: str | None = None
        # Star button registry — populated by _populate so favorite toggles
        # can update one row in place instead of rebuilding the entire tree.
        # Key → variant star button; key → (family_item, family_name, entries)
        # for the tri-state family star.
        self._star_buttons: dict[str, QToolButton] = {}
        self._family_records: dict[str, tuple[QTreeWidgetItem, str, list[ThemeGroupEntry]]] = {}

        self._setup_ui()
        # Seed the language combo after the widgets exist (set_language reads
        # self.language_combo); does not emit.
        self.set_language(ui_language)
        self._populate()
        self._sync_font_scale_combo()
        self._sync_zoom_combo()

    # ---- UI construction -------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout()
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)
        layout.setSpacing(SPACING.sm)

        self.install_issue_banner(layout)

        # Language row (restart-to-apply). Merged in from the former
        # LanguagePanel; Qt captures tr() strings at construction, so a language
        # change persists immediately but applies on next launch.
        lang_row = QHBoxLayout()
        lang_row.setSpacing(SPACING.sm)
        language_label = QLabel(self.tr("Language"))
        lang_row.addWidget(language_label)

        self.language_combo = QComboBox()
        self.language_combo.setObjectName("languageCombo")
        for code, name in available_languages().items():
            self.language_combo.addItem(name, code)
        # `activated` fires only on user interaction (not on the programmatic
        # setCurrentIndex in set_language).
        self.language_combo.activated.connect(self._on_language_selected)
        lang_row.addWidget(self.language_combo)
        # This panel builds its own rows instead of using FormPanel, so every
        # anchor is registered by hand. Providers read the labels live, so the
        # index follows the installed translator (see setting_anchor.py).
        self.register_setting("language", self.language_combo, lambda: (language_label.text(),))
        lang_row.addStretch(1)
        layout.addLayout(lang_row)

        # Hidden until the user changes language; restart-to-apply hint.
        self.language_restart_note = QLabel(self.tr("Restart to apply."))
        self.language_restart_note.setWordWrap(True)
        self.language_restart_note.setVisible(False)
        layout.addWidget(self.language_restart_note)

        # Zoom (whole-UI scale) row. Restart-to-apply (injected as
        # QT_SCALE_FACTOR at startup), so picking a value only persists +
        # reveals the restart note below — no live restyle.
        zoom_row = QHBoxLayout()
        zoom_row.setSpacing(SPACING.sm)

        zoom_tip = self.tr("Scale the entire interface — text, spacing, and controls. Applies after restart.")
        zoom_label = QLabel(self.tr("Zoom"))
        zoom_label.setToolTip(zoom_tip)
        zoom_row.addWidget(zoom_label)

        self.zoom_combo = QComboBox()
        self.zoom_combo.setObjectName("zoomCombo")
        self.zoom_combo.setToolTip(zoom_tip)
        for p in ZOOM_PRESETS:
            self.zoom_combo.addItem(tr_format(self.tr("%1%"), p), p)
        # `activated` (user-only) so the programmatic setCurrentIndex in
        # _sync_zoom_combo doesn't emit and falsely reveal the restart note.
        self.zoom_combo.activated.connect(self._on_zoom_selected)
        zoom_row.addWidget(self.zoom_combo)
        self.register_setting("zoom", self.zoom_combo, lambda: (zoom_label.text(), self.zoom_combo.toolTip()))

        zoom_row.addStretch(1)

        layout.addLayout(zoom_row)

        # Hidden until the user changes zoom; restart-to-apply hint (mirrors the
        # language note above).
        self.zoom_restart_note = QLabel(self.tr("Restart to apply."))
        self.zoom_restart_note.setWordWrap(True)
        self.zoom_restart_note.setVisible(False)
        layout.addWidget(self.zoom_restart_note)

        # Text size (global UI font scale) row. A styled QComboBox of discrete
        # percent presets; the selected percent maps to a float scale.
        # Restart-to-apply (D39b-A): the scale is baked into the one-time
        # structural stylesheet at boot, and changing it relayouts every widget
        # in the window, so there is no instant path the way there is for theme.
        font_row = QHBoxLayout()
        font_row.setSpacing(SPACING.sm)

        font_tip = self.tr("Scale all UI text. Applies after restart.")
        font_label = QLabel(self.tr("Text size"))
        font_label.setToolTip(font_tip)
        font_row.addWidget(font_label)

        self.font_scale_combo = QComboBox()
        self.font_scale_combo.setObjectName("fontScaleCombo")
        self.font_scale_combo.setToolTip(font_tip)
        for p in FONT_SCALE_PRESETS:
            self.font_scale_combo.addItem(tr_format(self.tr("%1%"), p), p)
        # `activated` fires only on user interaction; `currentIndexChanged`
        # would also fire on the programmatic setCurrentIndex in
        # _sync_font_scale_combo, falsely revealing the restart note.
        self.font_scale_combo.activated.connect(self._on_font_scale_selected)
        font_row.addWidget(self.font_scale_combo)
        self.register_setting(
            "text_size",
            self.font_scale_combo,
            lambda: (font_label.text(), self.font_scale_combo.toolTip()),
        )

        # Trailing stretch keeps the combo left-aligned next to its label
        # rather than spanning the full row width.
        font_row.addStretch(1)

        layout.addLayout(font_row)

        # Hidden until the user changes text size. Unlike the language/zoom
        # notes this one carries actions, because the reward is worth offering
        # rather than leaving the user to find the window button themselves.
        # Both are quiet variants: a settings note must not become the primary
        # action on the screen (D41).
        self.font_scale_restart_row = QWidget()
        font_note_layout = QHBoxLayout(self.font_scale_restart_row)
        font_note_layout.setContentsMargins(0, 0, 0, 0)
        font_note_layout.setSpacing(SPACING.sm)
        self.font_scale_restart_note = QLabel(self.tr("Text size will change after restart."))
        self.font_scale_restart_note.setWordWrap(True)
        font_note_layout.addWidget(self.font_scale_restart_note)
        self.restart_now_btn = ModernButton(self.tr("Restart now"), variant="secondary")
        self.restart_now_btn.clicked.connect(self._on_restart_now)
        font_note_layout.addWidget(self.restart_now_btn)
        self.restart_later_btn = ModernButton(self.tr("Later"), variant="ghost")
        self.restart_later_btn.clicked.connect(self._on_restart_later)
        font_note_layout.addWidget(self.restart_later_btn)
        font_note_layout.addStretch(1)
        self.font_scale_restart_row.setVisible(False)
        layout.addWidget(self.font_scale_restart_row)

        # File-dialog mode. Qt's built-in dialog is the default because the
        # OS-native one can hang the GUI thread on some Windows setups
        # (Explorer shell/cloud enumeration on a bad network — Issue #100).
        self.native_dialogs_checkbox = QCheckBox(self.tr("Use system file dialogs"))
        self.native_dialogs_checkbox.setToolTip(
            self.tr(
                "Use the operating system's native file pickers instead of the app's built-in ones. "
                "Native dialogs can freeze the app on some Windows systems with flaky network drives "
                "or cloud storage, which is why this is off by default."
            )
        )
        self.native_dialogs_checkbox.setChecked(self._use_native_file_dialogs)
        self.native_dialogs_checkbox.toggled.connect(self._on_native_dialogs_toggled)
        layout.addWidget(self.native_dialogs_checkbox)
        self.register_setting(
            "native_file_dialogs",
            self.native_dialogs_checkbox,
            lambda: (self.native_dialogs_checkbox.text(), self.native_dialogs_checkbox.toolTip()),
        )

        # Theme selection. The intro explains the tree's star/preview behavior,
        # so it sits just above the tree — the language/zoom/text-size controls
        # now lead the panel.
        intro = QLabel(
            self.tr(
                "Star themes to add them to the top-right selector. Click any row to preview — "
                "the change applies live across the app. Press <b>Revert</b> to undo your preview."
            )
        )
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(intro)

        self.tree = QTreeWidget(self)
        # objectName lets common.qss scope styling overrides to just this tree
        # without disturbing other trees in the app.
        self.tree.setObjectName("themesPanelTree")
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels([self.tr("Name"), self.tr("Status"), ""])
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
        # The theme list is one logical setting. Its rows are rebuilt on every
        # favorite toggle and profile switch, so search anchors the tree itself.
        self.register_setting("theme", self.tree, lambda: (intro.text(), self.open_folder_btn.text()))

        # Themes render exactly as their author wrote them (D43-A). This line is
        # the entire intervention: it states the measured ratio and nothing is
        # corrected, substituted or rejected. Empty (and hidden) when the live
        # theme measures fine.
        self.contrast_warning = QLabel()
        self.contrast_warning.setObjectName("helper-text")
        self.contrast_warning.setWordWrap(True)
        self.contrast_warning.setVisible(False)
        layout.addWidget(self.contrast_warning)

        buttons = QHBoxLayout()
        buttons.setSpacing(SPACING.sm)

        self.open_folder_btn = ModernButton(self.tr("Open themes folder"), variant="secondary")
        self.open_folder_btn.setToolTip(self._themes_folder_tooltip())
        self.open_folder_btn.clicked.connect(self._open_themes_folder)
        buttons.addWidget(self.open_folder_btn)

        self.revert_btn = ModernButton(self.tr("Revert"), variant="secondary")
        self.revert_btn.setToolTip(self.tr("Restore the theme that was active when this tab was opened."))
        self.revert_btn.clicked.connect(self._revert_preview)
        buttons.addWidget(self.revert_btn)

        buttons.addStretch()

        # Panel-level action, right-aligned past the stretch so it does not read
        # as a third theme button. It only asks; MainWindow owns the dialog,
        # because a profile switch reloads this panel from the new config.
        self.manage_profiles_btn = ModernButton(self.tr("Manage Profiles…"), variant="secondary")
        self.manage_profiles_btn.setToolTip(
            self.tr("Keep several complete settings snapshots and switch between them.")
        )
        self.manage_profiles_btn.clicked.connect(self._on_manage_profiles)
        buttons.addWidget(self.manage_profiles_btn)

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
            self._star_buttons.clear()
            self._family_records.clear()
            for family_name, entries in groups:
                if family_name is None:
                    # Standalone: render the single entry as a top-level row.
                    entry = entries[0]
                    item = QTreeWidgetItem(
                        [
                            entry.display_name,
                            self.tr("Active") if entry.key == active else "",
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
                                self.tr("Active") if entry.key == active else "",
                                "",
                            ]
                        )
                        child.setData(self.COL_NAME, Qt.ItemDataRole.UserRole, entry.key)
                        family_item.addChild(child)
                        star = self._build_star_cell(entry.key, entry.key in favorites)
                        self.tree.setItemWidget(child, self.COL_STAR, star)
                        self._family_records[entry.key] = (family_item, family_name, entries)
                        if entry.key == active:
                            self.tree.setCurrentItem(child)
                            active_inside = True

                    family_star = self._build_family_star_cell(family_name, entries, favorites)
                    self.tree.setItemWidget(family_item, self.COL_STAR, family_star)

                    if active_inside:
                        family_item.setExpanded(True)
        finally:
            self.tree.blockSignals(False)

        # One call covers populate, Revert and load_from_config: the latter two
        # both rebuild the tree through here.
        self._refresh_contrast_warning()

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
        button.setAccessibleName(self.tr("Unfavorite") if is_favorite else self.tr("Favorite"))
        button.setAutoRaise(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip(self.tr("Click to add to / remove from favorites."))
        # `key` captured per-row via default arg, sidesteps closure-over-loop-var.
        button.clicked.connect(lambda _checked=False, k=key: self._toggle_favorite(k))
        # Register so _refresh_favorite_state can update this row in place,
        # avoiding a full tree rebuild on every star click.
        self._star_buttons[key] = button

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
        wrapper = QWidget(self.tree)
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
            tooltip = tr_format(self.tr("Favorite all %1 %2 variants."), n_total, family_name)
            button.setStyleSheet(f"font-size: {font_size}px;")
        elif n_fav == n_total:
            button.setText(_STAR_FILLED)
            tooltip = tr_format(self.tr("Unfavorite all %1 %2 variants."), n_total, family_name)
            button.setStyleSheet(f"font-size: {font_size}px;")
        else:
            button.setText(_STAR_FILLED)
            tooltip = tr_format(
                self.tr("%1 of %2 %3 variants favorited. Click to favorite all."), n_fav, n_total, family_name
            )
            button.setStyleSheet(f"font-size: {font_size}px;")
            effect = QGraphicsOpacityEffect(button)
            effect.setOpacity(_FAMILY_STAR_PARTIAL_OPACITY)
            button.setGraphicsEffect(effect)

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
        if key != Theme.get_current_mode():
            Theme.set_mode(key)
            self._apply_to_app(key)
            # Avoid a full _populate() here — it rebuilt every row (including
            # QPainter-drawn star icons) on each preview click and made theme
            # switching feel laggy. The only visible mutation is the Active
            # marker moving between two rows; update just those.
            self._refresh_active_marker(key)
            self.state_changed.emit(Theme.get_current_mode(), Theme.get_favorites())
        # Outside the "already active" guard: re-selecting the live theme must
        # still restate its measured contrast rather than leave a stale line.
        self._refresh_contrast_warning(key)

    # ---- Contrast note ---------------------------------------------------

    def _refresh_contrast_warning(self, key: str | None = None) -> None:
        """Restate the measured contrast of ``key`` (default: the live theme).

        Read-only: it measures the colours the theme author wrote and says so.
        Nothing here may change, replace or refuse a colour — see D43-A and the
        note above ``assess_theme_contrast``.
        """
        colors = Theme.get_colors(key if key is not None else Theme.get_current_mode())
        text = self._contrast_warning_text(assess_theme_contrast(colors))
        self.contrast_warning.setText(text)
        self.contrast_warning.setVisible(bool(text))

    def _contrast_warning_text(self, issues: tuple[ContrastIssue, ...]) -> str:
        """Render ``issues`` as one sentence; empty string when there are none."""
        if not issues:
            return ""
        # (measured template, unmeasurable text) per role. Both must stay
        # literal tr() arguments — Qt extracts them statically.
        phrases = {
            CONTRAST_ROLE_PRIMARY_LABEL: (
                self.tr("button labels %1:1"),
                self.tr("button labels could not be measured"),
            ),
            CONTRAST_ROLE_MUTED_TEXT: (
                self.tr("muted text %1:1"),
                self.tr("muted text could not be measured"),
            ),
            CONTRAST_ROLE_SURFACE_EDGE: (
                self.tr("cards against the page %1:1"),
                self.tr("cards against the page could not be measured"),
            ),
        }
        details: list[str] = []
        for issue in issues:
            phrase = phrases.get(issue.role)
            if phrase is None:
                continue
            measured, unmeasurable = phrase
            details.append(unmeasurable if issue.ratio is None else tr_format(measured, f"{issue.ratio:.1f}"))
        return tr_format(
            self.tr("Low contrast, shown exactly as the theme author wrote it: %1."),
            ", ".join(details),
        )

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
                    descendant.setText(self.COL_STATUS, self.tr("Active") if key == new_active_key else "")

    def _toggle_favorite(self, key: str) -> None:
        """Star/unstar `key`, refresh the affected row, notify listeners."""
        if Theme.is_favorite(key):
            Theme.remove_favorite(key)
        else:
            Theme.add_favorite(key)
        self._refresh_favorite_state(key)
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
        # Refresh every variant button; the family cell is rebuilt once below.
        for key in keys:
            self._refresh_variant_star(key)
        self._refresh_family_star(keys[0] if keys else "")
        self.state_changed.emit(Theme.get_current_mode(), Theme.get_favorites())
        self.favorites_changed.emit()

    def _refresh_favorite_state(self, key: str) -> None:
        """Update the star button(s) affected by toggling ``key``.

        Mutates the existing variant button and rebuilds only the family
        star cell — avoids clearing the entire tree and recreating every
        row widget, which is what the old `_populate()` path did and what
        users were seeing as star-click lag.
        """
        self._refresh_variant_star(key)
        self._refresh_family_star(key)

    def _refresh_variant_star(self, key: str) -> None:
        button = self._star_buttons.get(key)
        if button is None:
            return
        is_fav = Theme.is_favorite(key)
        button.setChecked(is_fav)
        button.setText(_STAR_FILLED if is_fav else _STAR_OUTLINE)
        button.setAccessibleName(self.tr("Unfavorite") if is_fav else self.tr("Favorite"))

    def _refresh_family_star(self, key: str) -> None:
        """Rebuild only the family star cell for the family containing ``key``."""
        record = self._family_records.get(key)
        if record is None:
            return
        family_item, family_name, entries = record
        favorites = set(Theme.get_favorites())
        new_cell = self._build_family_star_cell(family_name, entries, favorites)
        self.tree.setItemWidget(family_item, self.COL_STAR, new_cell)

    def _themes_folder_tooltip(self) -> str:
        """Tooltip for the "Open themes folder" button, naming the current root.

        Shared by ``_setup_ui`` and ``load_from_config`` so the displayed path
        can follow a config swap without duplicating the translatable string.
        """
        return tr_format(
            self.tr("Open %1; drop theme JSON files here to install on next launch."),
            self._themes_root,
        )

    def _open_themes_folder(self) -> None:
        """Open (creating if necessary) the user themes directory.

        A failure here used to reach the log and nowhere else, so the button
        simply did nothing (D24). The repair offered is the *parent* folder, not
        a retry: an mkdir refused for permissions will be refused again, and the
        parent is where the user can see and fix why.
        """
        try:
            self._themes_root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("Could not create themes dir %s: %s", self._themes_root, e)
            parent = self._themes_root.parent

            def _open_parent() -> None:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(parent)))

            self.show_screen_issue(
                ScreenIssue(
                    summary=self.tr("The themes folder could not be opened."),
                    details=f"{self._themes_root}: {e}",
                    action_id="ui.themes-folder-parent",
                    action_text=self.tr("Open Parent Folder"),
                ),
                action=_open_parent,
            )
            return
        self.clear_screen_issue()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._themes_root)))

    def _on_manage_profiles(self) -> None:
        """Ask the window to open the settings-profile manager."""
        self.manage_profiles_requested.emit()

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
        # Single choke point for "the panel made this theme live" (preview and
        # Revert both route through here), so load_from_config can recognise a
        # later reload carrying this theme as the panel's own change rather than
        # an external swap. See _last_seen_theme.
        self._last_seen_theme = mode
        app = QApplication.instance()
        if isinstance(app, QApplication):
            Theme.apply_to_app(app, mode)

    # ---- Text size (font scale) -----------------------------------------

    def _sync_font_scale_combo(self) -> None:
        """Select the combo entry matching the *pending* config font scale.

        Deliberately not ``Theme.get_font_scale()``: text size is
        restart-to-apply, so the running Theme keeps the boot value all session
        while the combo has to show what the user chose and what was persisted.

        Signals are blocked so syncing from config state never emits and falsely
        reveals the restart note (belt-and-suspenders given ``activated`` is
        user-only). A legacy custom scale that is not one of
        ``FONT_SCALE_PRESETS`` snaps the display to the nearest preset.
        """
        value = round(self._ui_font_scale * 100)
        idx = self._nearest_preset_index(value)
        self.font_scale_combo.blockSignals(True)
        try:
            self.font_scale_combo.setCurrentIndex(idx)
        finally:
            self.font_scale_combo.blockSignals(False)

    def _nearest_preset_index(self, value: int) -> int:
        """Return the index of the font-scale preset closest to ``value`` percent."""
        return min(range(len(FONT_SCALE_PRESETS)), key=lambda i: abs(FONT_SCALE_PRESETS[i] - value))

    def _sync_zoom_combo(self) -> None:
        """Select the combo entry matching the persisted ``ui_zoom``.

        Signals are blocked so syncing from config state never emits and falsely
        reveals the restart note (belt-and-suspenders given ``activated`` is
        user-only). A value that is not one of ``ZOOM_PRESETS`` snaps to the
        nearest preset.
        """
        value = round(self._ui_zoom * 100)
        idx = min(range(len(ZOOM_PRESETS)), key=lambda i: abs(ZOOM_PRESETS[i] - value))
        self.zoom_combo.blockSignals(True)
        try:
            self.zoom_combo.setCurrentIndex(idx)
        finally:
            self.zoom_combo.blockSignals(False)

    def _on_zoom_selected(self, index: int) -> None:
        """Persist the zoom preset the user picked and reveal the restart note.

        No live restyle: zoom is injected as QT_SCALE_FACTOR before QApplication
        is built, so it only takes effect on the next launch.
        """
        percent = self.zoom_combo.itemData(index)
        if percent is None:
            return
        self._ui_zoom = int(percent) / 100.0
        self.zoom_restart_note.setVisible(True)
        self.zoom_changed.emit(self._ui_zoom)

    def _on_native_dialogs_toggled(self, checked: bool) -> None:
        """Persist the file-dialog mode change (applies immediately)."""
        self._use_native_file_dialogs = checked
        self.native_dialogs_changed.emit(checked)

    def _on_font_scale_selected(self, index: int) -> None:
        """Persist the preset the user picked and reveal the restart note.

        No live restyle (D39b-A). The old path called ``Theme.set_font_scale``
        and repolished the whole widget tree behind a wait cursor, which is the
        ~900 ms dead window this decision exists to remove; the scale is baked
        into the structural stylesheet compiled once at boot instead.
        """
        percent = self.font_scale_combo.itemData(index)
        if percent is None:
            return
        self._ui_font_scale = int(percent) / 100.0
        # A new choice always speaks up again, even after a previous `Later`.
        self._font_scale_note_dismissed = False
        self._refresh_font_scale_note()
        self.font_scale_changed.emit(self._ui_font_scale)

    def _refresh_font_scale_note(self) -> None:
        """Show the restart note exactly while the pending scale differs."""
        pending = self._ui_font_scale != self._boot_font_scale
        self.font_scale_restart_row.setVisible(pending and not self._font_scale_note_dismissed)

    def _on_restart_later(self) -> None:
        """Dismiss the note for this session; the choice stays persisted."""
        self._font_scale_note_dismissed = True
        self._refresh_font_scale_note()

    def _on_restart_now(self) -> None:
        """Relaunch the app so the new text size takes effect.

        The executable is resolved *first*: if we cannot name what to launch,
        nothing closes and the panel says so inline. Recoverable failures never
        open a modal (D24), and the banner this host already owns is the place
        for it.

        On success the intent is recorded and the ordinary ``close()`` runs, so
        the settings flush, worker cancellation/join, dictionary release and
        deferred-close handling all happen exactly as they do for a normal quit.
        The replacement is started by ``gui.app`` after ``app.exec()`` returns.
        A refused close (a tab vetoing it, or the user cancelling) clears the
        intent again so a later ordinary quit does not silently relaunch.
        """
        if restart.resolve_relaunch_target() is None:
            self.show_screen_issue(
                ScreenIssue(
                    summary=self.tr("Could not restart automatically. Close and reopen Anki Miner to apply it."),
                    details=self.tr("The Anki Miner executable could not be located from this process."),
                )
            )
            return
        self.clear_screen_issue()
        restart.request_restart()
        window = self.window()
        if window is not None and not window.close():
            restart.clear_restart_request()

    # ---- Language --------------------------------------------------------

    def set_language(self, code: str) -> None:
        """Select ``code`` in the language combo without emitting (external sync)."""
        idx = self.language_combo.findData(code, Qt.ItemDataRole.UserRole)
        if idx < 0:
            idx = self.language_combo.findData("en", Qt.ItemDataRole.UserRole)
        self.language_combo.blockSignals(True)
        try:
            self.language_combo.setCurrentIndex(max(0, idx))
        finally:
            self.language_combo.blockSignals(False)

    def _on_language_selected(self, index: int) -> None:
        """Persist the picked UI language and reveal the restart note.

        Restart-to-apply: Qt widgets capture their tr() strings at construction,
        so the change only takes effect on the next launch.
        """
        code = self.language_combo.itemData(index)
        if not isinstance(code, str):
            return
        self.language_restart_note.setVisible(True)
        self.language_changed.emit(code)

    # ---- External config reload -----------------------------------------

    def load_from_config(self, config: AnkiMinerConfig) -> None:
        """Repaint every control from ``config`` without emitting a signal.

        This panel is deliberately outside ``SettingsTab._save_panels`` (it
        persists through its own signals, not the Save round-trip), so nothing
        else repaints it when the whole config is replaced from the outside —
        Reset to Defaults, Import Settings, or any other ``update_config`` →
        ``config_refreshed`` fan-out. Without this the zoom/text-size combos,
        the native-dialogs checkbox and the theme tree keep showing the previous
        config's values and the user's next edit starts from a stale baseline.

        Every mutation here is signal-safe. The panel's change handlers feed
        ``config_changed`` → ``MainWindow.update_config``, so one unguarded
        ``setChecked``/``setCurrentIndex`` would write the panel's *stale* state
        straight back into the config being loaded.

        The active theme lives on the ``Theme`` singleton (the panel writes
        through it for live preview), so it is re-read from there rather than
        set here — callers that swap the whole config re-seed ``Theme`` before
        calling. Text size does not: it is restart-to-apply, so the running
        ``Theme`` scale is the *boot* value and the config carries the pending
        one.
        """
        # Blocks signals internally.
        self.set_language(config.ui_language)

        # Zoom has no live Theme state (it is injected as QT_SCALE_FACTOR before
        # QApplication exists), so the backing field is the source of truth.
        self._ui_zoom = config.ui_zoom
        self._sync_zoom_combo()  # blocks signals internally

        # Same shape as zoom: the pending config value drives the combo, and the
        # process keeps running at the boot scale until it is relaunched.
        self._ui_font_scale = config.ui_font_scale
        self._sync_font_scale_combo()  # blocks signals internally

        self._use_native_file_dialogs = config.use_native_file_dialogs
        self.native_dialogs_checkbox.blockSignals(True)
        try:
            self.native_dialogs_checkbox.setChecked(config.use_native_file_dialogs)
        finally:
            self.native_dialogs_checkbox.blockSignals(False)

        # The themes folder button and its tooltip must name the config's root;
        # left alone it would open (and create) the PREVIOUS config's directory.
        # This panel never re-scans the root itself, because discovery belongs to
        # Theme and re-runs only inside Theme.initialize — at boot (app.py) and
        # in the profile switch's whole-config re-seed, which runs BEFORE this
        # fan-out. So a config swap already arrives with the incoming root
        # discovered and _populate below renders it; what still cannot happen
        # live is picking up JSON files dropped into the folder mid-session.
        self._themes_root = config.themes_root
        self.open_folder_btn.setToolTip(self._themes_folder_tooltip())

        # Rebuild the tree so the Active marker, favorites stars and selection
        # follow the re-seeded Theme. _populate blocks the tree's signals, so no
        # state_changed escapes. Unconditional: favorites (and, for a whole-config
        # swap, the entire Theme state) can move without config.theme changing.
        self._populate()
        # Re-point Revert at the now-active theme; reverting to the pre-swap one
        # would fight the config that was just loaded. Two guards:
        #   * never before the first show — showEvent owns that first capture,
        #     and SettingsTab._load_config also runs during construction;
        #   * only when the incoming theme is not one this panel itself made
        #     live. A reload can be triggered by ANY non-external field (e.g.
        #     toggling "Use system file dialogs"), and it carries the previewed
        #     theme along with it; resetting there would silently destroy the
        #     revert target mid-preview and leave Revert a no-op.
        if self._preview_baseline is not None and config.theme != self._last_seen_theme:
            self.reset_baseline()
        self._last_seen_theme = config.theme

        self.language_restart_note.setVisible(config.ui_language != self._boot_language)
        self.zoom_restart_note.setVisible(config.ui_zoom != self._boot_zoom)
        self._refresh_font_scale_note()
