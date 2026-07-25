"""Header widget for main window.

Provides app branding, settings-profile and theme selection, and quick status
indicators.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QFontMetrics
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from anki_miner.gui.resources.styles import FONT_SIZES, SPACING
from anki_miner.gui.resources.styles.theme import Theme
from anki_miner.gui.utils.qt_helpers import install_no_scroll_on_inputs
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from anki_miner.gui.utils.profile_store import Profile

# Sentinel item data marking the "All themes…" entry that opens the Themes
# settings tab instead of switching themes. Picked to be distinct from any
# real theme key (which are filename stems — no leading underscore).
ALL_THEMES_SENTINEL = "__open_theme_settings__"

# Sentinel item data marking the "Manage profiles…" entry that opens the profile
# manager instead of switching profiles. Mirrors ALL_THEMES_SENTINEL; distinct
# from any real profile id, which is ``slugify`` output ([a-z0-9-] only).
MANAGE_PROFILES_SENTINEL = "__open_profile_manager__"

# Hard ceiling on the profile combo, in device-independent pixels. A profile
# name is user-supplied free text, so without a cap one long name would push the
# header — and with it the window's minimum width — out. Two independent layers
# hold it: the combo sizes itself from _PROFILE_COMBO_MIN_CHARS rather than from
# its widest item (so its sizeHint is content-independent), and this maximum
# width is the backstop for a very large UI font.
PROFILE_COMBO_MAX_WIDTH = 220
_PROFILE_COMBO_MIN_CHARS = 12

# Budget for the name text itself. Longer names are elided into it for display;
# the full name is kept on the item's ToolTipRole and the id in its itemData, so
# nothing is lost. Bounds the drop-down list, which sizes to its widest item
# regardless of the combo's own size-adjust policy.
_PROFILE_NAME_MAX_WIDTH = 150


class HeaderWidget(QWidget):
    """Header widget with app branding, profile and theme selection.

    The theme selector shows only the user's favorited themes plus an
    "All themes…" sentinel that opens the Themes tab in Settings. This keeps
    the top-right rotation focused even when many themes are installed.

    The settings-profile selector is populated entirely from the outside via
    :meth:`set_profiles` and stays hidden until there are at least two profiles,
    so a user who never creates one sees no change to the header.
    """

    # Active theme changed via this widget (theme key emitted).
    theme_changed = pyqtSignal(str)
    # User picked the "All themes…" sentinel — open the Themes settings tab.
    open_theme_settings = pyqtSignal()
    # Active settings profile changed via this widget (profile id emitted).
    profile_changed = pyqtSignal(str)
    # User picked the "Manage profiles…" sentinel — open the profile manager.
    open_profile_manager = pyqtSignal()

    def __init__(self, parent=None):
        """Initialize the header widget.

        Args:
            parent: Optional parent widget
        """
        super().__init__(parent)
        # Id the combo snaps back to when the sentinel is picked or a switch is
        # refused. set_profiles is its ONLY writer — see _on_profile_changed.
        self._active_profile_id: str | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        layout = QHBoxLayout()
        layout.setContentsMargins(SPACING.md, SPACING.sm, SPACING.md, SPACING.sm)

        # Left side: App branding
        branding_layout = QVBoxLayout()
        branding_layout.setSpacing(2)

        # App title
        title_label = QLabel("Anki Miner")
        title_font = QFont()
        title_font.setPixelSize(FONT_SIZES.h2)
        title_font.setWeight(QFont.Weight.Bold)
        title_label.setFont(title_font)
        title_label.setObjectName("heading2")
        branding_layout.addWidget(title_label)

        layout.addLayout(branding_layout)
        layout.addStretch()

        # Right side: settings-profile selector, then theme selector. Creation
        # order IS tab order, so building the profile block first gives
        # profile -> theme for free; keep it that way.
        profile_layout = QHBoxLayout()
        profile_layout.setSpacing(SPACING.xs)

        # "Settings profile:", not "Profile:": in an app whose whole job is
        # talking to Anki, a bare "Profile" reads as an Anki user profile.
        self.profile_label = QLabel(self.tr("Settings profile:"))
        self.profile_label.setObjectName("caption")
        profile_layout.addWidget(self.profile_label)

        self.profile_combo = QComboBox()
        # Same wheel hazard as the theme combo below, with a worse payload: a
        # stray scroll here would swap every setting in the app.
        self.profile_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Size from a fixed character budget rather than from the widest item,
        # so a long profile name cannot widen the header. See
        # PROFILE_COMBO_MAX_WIDTH.
        self.profile_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.profile_combo.setMinimumContentsLength(_PROFILE_COMBO_MIN_CHARS)
        self.profile_combo.setMaximumWidth(PROFILE_COMBO_MAX_WIDTH)
        # A control that silently rewrites every setting must announce itself:
        # without these a screen reader reads only "combo box" (see a1d78b72).
        self.profile_combo.setAccessibleName(self.tr("Settings profile"))
        self.profile_combo.setAccessibleDescription(
            self.tr("Switches every Anki Miner setting to the selected profile.")
        )
        self.profile_combo.setToolTip(
            self.tr(
                "Active settings profile. Switching swaps every setting; "
                "pick 'Manage profiles…' to add, rename or remove them."
            )
        )
        self.profile_label.setBuddy(self.profile_combo)
        profile_layout.addWidget(self.profile_combo)

        # Starts empty, which hides the whole block: an existing user who never
        # creates a profile sees no change to the header at all.
        self.set_profiles((), None)
        self.profile_combo.currentIndexChanged.connect(self._on_profile_changed)

        layout.addLayout(profile_layout)

        theme_layout = QHBoxLayout()
        theme_layout.setSpacing(SPACING.xs)

        theme_label = QLabel(self.tr("Theme:"))
        theme_label.setObjectName("caption")
        theme_layout.addWidget(theme_label)

        self.theme_combo = QComboBox()
        self.theme_combo.setAccessibleName(self.tr("Theme"))
        # Issue #99's hazard, with an unusually expensive payload: a wheel over
        # this combo changes theme, and each change costs a measured ~870ms
        # whole-app stylesheet repolish on the GUI thread. Without StrongFocus a
        # single scroll gesture fires several of them back to back. StrongFocus
        # alone is not enough — QComboBox::wheelEvent is gated on the
        # SH_ComboBox_AllowWheelScrolling style hint, not on focus — so the
        # event-filter sweep below is the layer that actually eats the wheel.
        self.theme_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._populate_theme_combo()
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        theme_layout.addWidget(self.theme_combo)

        layout.addLayout(theme_layout)

        self.setLayout(layout)
        self.setObjectName("header-widget")

        # MUST run after setLayout: a widget added to a not-yet-installed
        # layout is not reparented onto the container, so before this line
        # findChildren(QComboBox) is empty and the sweep silently installs the
        # filter on nothing. Keep this call last in _setup_ui.
        install_no_scroll_on_inputs(self)

    def _populate_theme_combo(self) -> None:
        """Rebuild the combo from current favorites + active theme + sentinel.

        Signals are blocked during the rebuild so callers can refresh after a
        favorites change without re-triggering theme apply.
        """
        self.theme_combo.blockSignals(True)
        try:
            self.theme_combo.clear()

            favorites = Theme.get_favorited_themes()
            current_mode = Theme.get_current_mode()
            available = Theme.get_available_themes()

            # If the active theme isn't in favorites (e.g. user unstarred it),
            # show it at the top so the dropdown still reflects reality and
            # the user isn't suddenly "missing" a theme they're actively using.
            if current_mode and current_mode not in favorites:
                display = available.get(current_mode, current_mode)
                self.theme_combo.addItem(display, current_mode)

            for key, display in favorites.items():
                self.theme_combo.addItem(display, key)

            # Sentinel entry that opens Settings → Themes.
            self.theme_combo.addItem(self.tr("All themes…"), ALL_THEMES_SENTINEL)

            # Select active theme.
            for i in range(self.theme_combo.count()):
                if self.theme_combo.itemData(i) == current_mode:
                    self.theme_combo.setCurrentIndex(i)
                    break

            tooltip_names = ", ".join(available.values())
            self.theme_combo.setToolTip(
                tr_format(
                    self.tr(
                        "Active theme. Top-right shows favorites; pick 'All themes…' to manage them. "
                        "(Ctrl+T cycles favorites). Installed: %1"
                    ),
                    tooltip_names,
                )
            )
        finally:
            self.theme_combo.blockSignals(False)

    def _on_theme_changed(self, index: int) -> None:
        """Handle theme selection change.

        Args:
            index: Selected combo box index
        """
        data = self.theme_combo.itemData(index)
        if data == ALL_THEMES_SENTINEL:
            # Snap selection back to the active theme so the sentinel never
            # appears "selected" in the closed combo.
            self.update_theme_selector()
            self.open_theme_settings.emit()
            return
        if data:
            Theme.set_mode(data)
            self.theme_changed.emit(data)

    def update_theme_selector(self) -> None:
        """Update theme selector to match current theme without re-emitting."""
        current_theme = Theme.get_current_mode()
        for i in range(self.theme_combo.count()):
            if self.theme_combo.itemData(i) == current_theme:
                self.theme_combo.blockSignals(True)
                self.theme_combo.setCurrentIndex(i)
                self.theme_combo.blockSignals(False)
                return

        # Active theme not in combo (favorites changed and dropped it).
        # Rebuild so the active theme reappears at the top.
        self.refresh_favorites()

    def refresh_favorites(self) -> None:
        """Rebuild the combo after favorites have changed.

        Call this from MainWindow whenever the Themes settings panel mutates
        the favorites list, so the top-right selector stays in sync.
        """
        self._populate_theme_combo()

    # ------------------------------------------------------------------
    # Settings profiles
    # ------------------------------------------------------------------

    def set_profiles(self, profiles: Sequence[Profile], active_id: str | None) -> None:
        """Rebuild the profile combo and point it at ``active_id``.

        The single entry point for the profile block: it owns the item list, the
        selection, ``_active_profile_id`` and the block's visibility. Idempotent,
        and safe with an empty sequence.

        **Emits nothing** — not even when the rebuild moves the selection. That
        is a hard contract, not tidiness: ``ProfileController`` calls this from a
        ``finally`` on EVERY terminal path of ``switch_to``, the success path
        included, so a ``currentIndexChanged`` escaping the rebuild would
        re-enter ``switch_to`` before the first call had returned. It is also the
        snap-back path after a REFUSED switch — ``currentIndexChanged`` has
        already moved the combo to B by the time a refusal is decided — so it
        re-selects from scratch rather than assuming the combo is already right.

        Args:
            profiles: Stored profiles, in display order.
            active_id: Id of the live profile, or ``None`` when the session
                could not be attributed to one.
        """
        self._active_profile_id = active_id

        self.profile_combo.blockSignals(True)
        try:
            self.profile_combo.clear()
            metrics = QFontMetrics(self.profile_combo.font())
            for profile in profiles:
                display = metrics.elidedText(profile.name, Qt.TextElideMode.ElideRight, _PROFILE_NAME_MAX_WIDTH)
                self.profile_combo.addItem(display, profile.id)
                # The FULL name goes on the tooltip: `display` may be elided, so
                # this is the only place a long name survives in the UI.
                self.profile_combo.setItemData(
                    self.profile_combo.count() - 1, profile.name, Qt.ItemDataRole.ToolTipRole
                )
            self.profile_combo.addItem(self.tr("Manage profiles…"), MANAGE_PROFILES_SENTINEL)
            self._select_active_locked()
        finally:
            self.profile_combo.blockSignals(False)

        # One profile is indistinguishable from none, so the block stays out of
        # the header until there is a choice to make.
        visible = len(profiles) >= 2
        self.profile_label.setVisible(visible)
        self.profile_combo.setVisible(visible)

    def _on_profile_changed(self, index: int) -> None:
        """Handle profile selection change.

        Deliberately does NOT update ``_active_profile_id``: the selection is a
        request, not an outcome. The controller refuses switches (mining is
        running, the file is unreadable, the commit did not persist) and calls
        :meth:`set_profiles` back on every terminal path, so letting it be the
        sole writer is what makes the snap-back point at the profile that is
        actually live rather than at the one the user clicked.

        Args:
            index: Selected combo box index
        """
        data = self.profile_combo.itemData(index)
        if data == MANAGE_PROFILES_SENTINEL:
            # Snap selection back to the active profile so the sentinel never
            # appears "selected" in the closed combo.
            self._select_active()
            self.open_profile_manager.emit()
            return
        if data:
            self.profile_changed.emit(data)

    def _select_active(self) -> None:
        """Select the active profile without re-emitting."""
        self.profile_combo.blockSignals(True)
        try:
            self._select_active_locked()
        finally:
            self.profile_combo.blockSignals(False)

    def _select_active_locked(self) -> None:
        """Select the active profile; the caller MUST already hold the block.

        Split from :meth:`_select_active` because ``blockSignals`` is a plain
        flag, not a counter: a nested block/unblock pair would unblock the combo
        halfway through an outer rebuild that is relying on it.
        """
        for index in range(self.profile_combo.count()):
            if self.profile_combo.itemData(index) == self._active_profile_id:
                self.profile_combo.setCurrentIndex(index)
                return

        # No active id, or one with no stored file (deleted outside the app, or
        # a boot whose reconcile could not attribute the live config). Land on
        # the first real profile so the sentinel is never left showing.
        for index in range(self.profile_combo.count()):
            if self.profile_combo.itemData(index) != MANAGE_PROFILES_SENTINEL:
                self.profile_combo.setCurrentIndex(index)
                return
