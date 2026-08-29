"""Header widget for main window.

Provides app branding, settings-profile and theme selection, and quick status
indicators.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QFontMetrics
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

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

# Ceiling on the profile combo, as a CHARACTER budget rather than a pixel one. A
# profile name is user-supplied free text, so without a cap one long name would
# push the header — and with it the window's minimum width — out. Two independent
# layers hold it: the combo sizes itself from _PROFILE_COMBO_MIN_CHARS rather
# than from its widest item (so its sizeHint is content-independent), and this
# budget is the backstop for a very large UI font.
#
# Measured in the combo's own font on every set_profiles rather than frozen as
# pixels: the 12-character hint alone is 160px at ui_font_scale 1.0 but 256px at
# 2.0, so the flat 220px this replaces clamped the combo BELOW the width it was
# sized for — truncating the text exactly when the user had asked for bigger.
_PROFILE_COMBO_MIN_CHARS = 12
_PROFILE_COMBO_MAX_CHARS = 20

# Budget for the name text itself. Longer names are elided into it for display;
# the full name is kept on the item's ToolTipRole and the id in its itemData, so
# nothing is lost. Bounds the drop-down list, which sizes to its widest item
# regardless of the combo's own size-adjust policy.
_PROFILE_NAME_MAX_WIDTH = 150

# Same character budget for the theme combo, and for the same reason: without it
# the combo sizes to its widest ITEM, which is the "Browse all N themes…"
# sentinel, making it the widest thing in the header (288px against the profile
# combo's 232px at ui_font_scale 1.5 on DejaVu Sans). That pushed the header
# minimum to 1028px -- past the 1024px WINDOW_MIN_WIDTH the window sets on
# itself -- on any desktop whose default sans face is Latin-only, because DejaVu
# advances run wider than Noto Sans CJK JP's for the same string.
#
# Capping the CLOSED combo costs nothing: the sentinel is never the closed text.
# Picking it opens Settings and snaps the selection straight back to the active
# theme, so the closed combo only ever shows a theme name. The drop-down keeps
# sizing to its widest item (291px against the 260px combo at 1.5x), so the
# sentinel stays readable where it is actually read.
#
# The same 12 as the profile combo, so the two selectors stack to one width.
# Measured: 12 and 10 land on the same 232px -- the style's own minimum binds
# first -- so this is the larger budget of the two that cost nothing.
_THEME_COMBO_MIN_CHARS = 12


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
    # User clicked the mining-language chip — open the selector in Settings.
    open_mining_language_settings = pyqtSignal()

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
        # Vertical margins are deliberately tighter than horizontal: in an
        # 800px-tall window height is the scarce axis, and this row sits above
        # every screen. Horizontal breathing room costs nothing by comparison.
        layout.setContentsMargins(SPACING.md, SPACING.xxs, SPACING.md, SPACING.xxs)

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

        # Right side: settings-profile selector, mining-language chip, then
        # theme selector. Creation order IS tab order, so building the blocks in
        # this order gives profile -> language -> theme for free; keep it that
        # way.
        profile_layout = QHBoxLayout()
        profile_layout.setSpacing(SPACING.xs)

        # "Settings profile:", not "Profile:": in an app whose whole job is
        # talking to Anki, a bare "Profile" reads as an Anki user profile.
        self.profile_label = QLabel(self.tr("Settings profile:"))
        self.profile_label.setObjectName("caption")
        profile_layout.addWidget(self.profile_label)

        self.profile_combo = QComboBox()
        # Styled by id in common.qss so this combo keeps its resting border in
        # every focus state — see the theme combo below for why.
        self.profile_combo.setObjectName("profile-combo")
        # Same wheel hazard as the theme combo below, with a worse payload: a
        # stray scroll here would swap every setting in the app.
        self.profile_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Size from a fixed character budget rather than from the widest item,
        # so a long profile name cannot widen the header. See
        # _PROFILE_COMBO_MAX_CHARS.
        self.profile_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.profile_combo.setMinimumContentsLength(_PROFILE_COMBO_MIN_CHARS)
        # A control that silently rewrites every setting must announce itself:
        # without these a screen reader reads only "combo box" (see a1d78b72).
        self.profile_combo.setAccessibleName(self.tr("Settings profile"))
        self.profile_combo.setAccessibleDescription(
            self.tr("Switches every Anki Miner setting to the selected profile.")
        )
        # Assigned before the set_profiles below, which reads it: with an active
        # profile the combo's tooltip LEADS with that profile's full name, so an
        # elided name is readable without opening the drop-down (the per-item
        # ToolTipRole only ever surfaces inside the popup).
        self._profile_tooltip = self.tr(
            "Active settings profile. Switching swaps every setting; "
            "pick 'Manage profiles…' to add, rename or remove them."
        )
        self.profile_label.setBuddy(self.profile_combo)
        profile_layout.addWidget(self.profile_combo)

        layout.addLayout(profile_layout)

        mining_language_layout = QHBoxLayout()
        mining_language_layout.setSpacing(SPACING.xs)

        # "Mining language", never "Language": the app also has an interface
        # language, and the two axes are independent (spec 7).
        self.mining_language_label = QLabel(self.tr("Mining language:"))
        self.mining_language_label.setObjectName("caption")
        mining_language_layout.addWidget(self.mining_language_label)

        # A button, not a combo: one writable selector for a control that swaps
        # every mining setting is enough, and it lives on the Settings page that
        # shows what the switch will change. This is a readout plus a shortcut.
        self.mining_language_button = QPushButton("")
        self.mining_language_button.setObjectName("mining-language-chip")
        self.mining_language_button.setFlat(True)
        self.mining_language_button.setAccessibleName(self.tr("Mining language"))
        self.mining_language_button.clicked.connect(self.open_mining_language_settings)
        self.mining_language_label.setBuddy(self.mining_language_button)
        mining_language_layout.addWidget(self.mining_language_button)

        # Both explicit minimums are what keeps this block OUT of the window's
        # minimum width. Without them the header demands its own hint — measured
        # at 1.5x text on DejaVu Sans, 972px -> 1258px against a WINDOW_MIN_WIDTH
        # contract of 1024 — and the window minimum follows it, which
        # test_layout_hostile_scale pins. The two combos cannot give that space
        # back (a combo's minimum ignores its items), so the chip is the block
        # that yields: it is a readout with a writable twin on the Settings page,
        # and the last thing in this row worth reserving space for.
        self.mining_language_label.setMinimumWidth(1)
        self.mining_language_button.setMinimumWidth(1)

        layout.addLayout(mining_language_layout)

        theme_layout = QHBoxLayout()
        theme_layout.setSpacing(SPACING.xs)

        theme_label = QLabel(self.tr("Theme:"))
        theme_label.setObjectName("caption")
        theme_layout.addWidget(theme_label)

        self.theme_combo = QComboBox()
        # These two combos are the first focusable widgets in the window, so
        # Qt's focus wrap-around lands here whenever something elsewhere hides,
        # disables or destroys the focused widget — which lit an accent border
        # in the corner for a click that happened in Settings. common.qss keeps
        # their border at rest in every focus state; the object name is what
        # that rule selects on.
        self.theme_combo.setObjectName("theme-combo")
        # Size from a fixed character budget rather than from the widest item --
        # see _THEME_COMBO_MIN_CHARS for why the widest item is the wrong ruler.
        self.theme_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.theme_combo.setMinimumContentsLength(_THEME_COMBO_MIN_CHARS)
        self.theme_combo.setAccessibleName(self.tr("Theme"))
        # Issue #99's hazard, with an unusually expensive payload: a wheel over
        # this combo changes theme, and each change costs a re-measured 1647 ms
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

        # AFTER setLayout, for the same reparenting reason as the sweep below: a
        # combo that is not yet a child of a laid-out widget reports the plain
        # application font instead of the stylesheet's scaled one, so both the
        # width cap and the elision metrics set_profiles measures would be taken
        # against the wrong font. Starting empty also hides the whole block, so
        # an existing user who never creates a profile sees no change here.
        self.set_profiles((), None)
        self.profile_combo.currentIndexChanged.connect(self._on_profile_changed)

        # Start hidden for the same reason the profile block does: a build
        # offering one language shows no chip at all.
        self.set_mining_language("", choices=0)

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

            # Sentinel entry that opens Settings → Themes. The count is the
            # point: this combo lists favorites only, which ships as two
            # entries, so without a number the app presents itself as having
            # two themes rather than the full shipped set.
            self.theme_combo.addItem(
                tr_format(self.tr("Browse all %1 themes…"), len(available)),
                ALL_THEMES_SENTINEL,
            )

            # Select active theme.
            for i in range(self.theme_combo.count()):
                if self.theme_combo.itemData(i) == current_mode:
                    self.theme_combo.setCurrentIndex(i)
                    break

            # The count now rides on the sentinel item, so the comma-joined dump
            # of every installed name is redundant here.
            self.theme_combo.setToolTip(
                self.tr("Active theme. This list shows your favorites; pick 'Browse all themes…' to see previews.")
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

        # Both measurements below (elision budget, width cap) read the combo's
        # font, and an unpolished widget reports the plain application font
        # rather than the stylesheet's ui_font_scale-derived one — measured, a
        # scale-2.0 combo answers with a 6px advance until it is polished, then
        # 14px. Polishing first is what makes the numbers the real ones.
        self.profile_combo.ensurePolished()

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

        # Re-measured here, not frozen at construction: this runs again after the
        # app stylesheet has been applied (and after every ui_font_scale change
        # that repolishes it), which is the only point the combo's real font is
        # known.
        self.profile_combo.setMaximumWidth(self._profile_combo_max_width(metrics))
        self._sync_profile_tooltip(profiles, active_id)

        # Visible as soon as ONE profile exists. ``ProfileController.bootstrap``
        # adopts the live config as a single "Default", so the old two-profile
        # rule kept the picker out of the header forever for anyone who never
        # hand-created a second one — and the picker is where the feature is
        # discovered. An EMPTY sequence still hides the block: that means the
        # profiles directory could not be enumerated, so the combo holds nothing
        # but the sentinel.
        visible = bool(profiles)
        self.profile_label.setVisible(visible)
        self.profile_combo.setVisible(visible)

    def set_mining_language(self, display_name: str, *, choices: int) -> None:
        """Show the live mining language, or hide the chip entirely.

        Hidden below two buildable languages: a chip that can only ever read
        日本語 is chrome describing the absence of a choice.
        """
        visible = choices >= 2 and bool(display_name)
        self.mining_language_label.setVisible(visible)
        self.mining_language_button.setVisible(visible)
        self.mining_language_button.setText(display_name)
        self.mining_language_button.setToolTip(
            tr_format(
                self.tr("Mining language: %1. Opens the selector in Settings."),
                display_name,
            )
        )

    def _profile_combo_max_width(self, metrics: QFontMetrics) -> int:
        """Backstop width for the profile combo, in the combo's CURRENT font.

        ``sizeHint`` is content-independent here (the size-adjust policy sizes
        the combo from ``_PROFILE_COMBO_MIN_CHARS``, not from its widest item),
        so it is a clean measure of the chrome — frame, arrow, margins — wrapped
        around that many characters. Widening it by the remaining characters in
        the same font keeps the cap a fixed CHARACTER budget at every
        ``ui_font_scale``, which a frozen pixel count cannot be.
        """
        extra = _PROFILE_COMBO_MAX_CHARS - _PROFILE_COMBO_MIN_CHARS
        return self.profile_combo.sizeHint().width() + extra * metrics.horizontalAdvance("x")

    def _sync_profile_tooltip(self, profiles: Sequence[Profile], active_id: str | None) -> None:
        """Lead the combo's tooltip with the active profile's FULL name.

        The per-item ``ToolTipRole`` set above only surfaces inside the open
        drop-down, so on the closed combo — the one place a name is actually
        elided — a widget-level tooltip is the only way the full name is
        readable at all. Without this the generic explanation shadowed it.
        """
        name = next((profile.name for profile in profiles if profile.id == active_id), None)
        self.profile_combo.setToolTip(f"{name}\n\n{self._profile_tooltip}" if name else self._profile_tooltip)

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
        if self._active_profile_id is not None:
            for index in range(self.profile_combo.count()):
                if self.profile_combo.itemData(index) == self._active_profile_id:
                    self.profile_combo.setCurrentIndex(index)
                    return

        # No active id, or one naming no listed profile (its file was deleted
        # outside the app, or a boot whose reconcile could not attribute the live
        # config). Select NOTHING rather than landing on the first profile:
        # displaying one as active while it is not makes it the single entry the
        # user cannot switch to, because the combo is already sitting on it and
        # clicking it emits no currentIndexChanged. An empty combo is honest
        # about the session belonging to no profile, and leaves every entry one
        # click away.
        self.profile_combo.setCurrentIndex(-1)
