"""UI settings panel — language, zoom, text size, and theme selection.

This is the "UI" Settings sub-tab. Top to bottom it offers:

* UI language picker (restart-to-apply; merged in from the former
  ``LanguagePanel``). Emits ``language_changed``.
* Zoom (whole-UI scale) and Text size, both restart-to-apply (D39b-A). Text size
  commits instantly and offers *Restart now* / *Later*; changing it relayouts the
  whole window, so unlike theme there is no instant path to have.
* The theme gallery (shipped + user-installed), rendered as preview cards by
  ``ThemeGalleryWidget``, with:
  - Live preview when a card is clicked — the active theme actually changes so
    the user sees buttons, tables, scrollbars, banners react in real time.
  - A star toggle to add/remove the theme from the favorites list that drives
    the top-right header combo and the Ctrl+T cycle rotation.
  - An "Open themes folder" button that surfaces ``~/.anki_miner/themes/`` so
    community-contributed JSON files can be installed by drop-in (see
    discussion #27).
  - A "Revert" button that snaps back to whatever was active when the user
    opened the panel — preview safety without a separate Apply/Cancel button.
  - A contrast note under the gallery, stating the measured ratio when the
    live theme is hard to read. Advisory only: the theme still renders
    exactly as its author wrote it (D43-A).

Persistence is handled by emitting ``state_changed`` / ``font_scale_changed`` /
``zoom_changed`` / ``language_changed`` (re-uses the ``config_changed``
convention from other panels). The settings tab forwards to
``MainWindow.update_config`` which writes ``gui_config.json``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
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
    assess_theme_contrast,
)
from anki_miner.gui.widgets.base import ScreenIssue, ScreenIssueHost, SettingAnchorHost
from anki_miner.gui.widgets.enhanced import ModernButton, ThemeGalleryWidget
from anki_miner.gui.widgets.enhanced.theme_preview import clear_thumbnail_cache
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)


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


def _window_is_shutting_down(window: QWidget) -> bool:
    """True when ``close()`` returned ``False`` because the close was DEFERRED.

    ``MainWindow`` refuses the close event while worker threads outlive the join
    grace: it hides itself, keeps the running QThreads alive and quits from a
    poll once the last one exits. ``QWidget.close()`` therefore reports the same
    ``False`` for a shutdown that is still going to happen as for a refusal.
    Duck-typed rather than imported — ``main_window`` imports this panel.
    """
    probe = getattr(window, "is_shutting_down", None)
    return callable(probe) and bool(probe())


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
    """

    ANCHOR_NAMESPACE = "ui"

    state_changed = pyqtSignal(str, tuple)
    favorites_changed = pyqtSignal()
    font_scale_changed = pyqtSignal(float)
    zoom_changed = pyqtSignal(float)
    language_changed = pyqtSignal(str)
    native_dialogs_changed = pyqtSignal(bool)

    def __init__(
        self,
        themes_root: Path,
        ui_zoom: float = 1.0,
        ui_language: str = "en",
        use_native_file_dialogs: bool = True,
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
                checkbox (native pickers are the default).
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
        # (theme key, favorites, themes_root) as of the state the gallery
        # widgets currently, honestly show — updated by both a full _populate()
        # rebuild and _on_theme_activated's surgical marker move. Boot
        # constructs this panel then immediately calls load_from_config with
        # the same boot config, and both used to rebuild the ~150-200-widget
        # gallery for nothing — see _populate.
        self._populated_state: tuple[str, tuple[str, ...], Path] | None = None

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
        self.font_scale_restart_note = QLabel(self.tr("Restart to apply."))
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

        # File-dialog mode. The OS-native picker is the default; the pickers are
        # non-blocking, so the Issue #100 freeze that once forced Qt's own
        # dialog can no longer happen (see gui/utils/file_dialogs).
        self.native_dialogs_checkbox = QCheckBox(self.tr("Use system file dialogs"))
        self.native_dialogs_checkbox.setToolTip(
            self.tr(
                "Use the operating system's native file pickers. Turn this off to use the app's "
                "built-in picker instead, which follows the app's theme and looks the same on "
                "every platform."
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

        # Theme selection. Same position in the panel as the list it replaces;
        # the intro explains the card behaviour, so it sits directly above.
        intro = QLabel(
            self.tr(
                "Click a theme preview to apply it live; <b>Revert</b> undoes it. "
                "Star themes to add them to the top-right selector."
            )
        )
        intro.setObjectName("helper-text")
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(intro)

        self.gallery = ThemeGalleryWidget(self)
        self.gallery.theme_activated.connect(self._on_theme_activated)
        self.gallery.favorite_toggled.connect(self._toggle_favorite)
        self.gallery.family_favorites_toggled.connect(self._toggle_family_favorites)
        layout.addWidget(self.gallery, 1)
        # The theme list is one logical setting. Its cards are rebuilt on every
        # profile switch, so search anchors the gallery itself.
        self.register_setting("theme", self.gallery, lambda: (intro.text(), self.open_folder_btn.text()))

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

        layout.addLayout(buttons)

        self.setLayout(layout)

    # ---- Population ------------------------------------------------------

    def _gallery_state(self) -> tuple[str, tuple[str, ...], Path]:
        """The (theme key, favorites, themes_root) the gallery should show.

        Shared by the ``_populate`` guard and by ``_on_theme_activated``'s
        surgical update, which must keep ``_populated_state`` truthful without
        a full rebuild — see the note there.
        """
        return (Theme.get_current_mode(), Theme.get_favorites(), self._themes_root)

    def _populate(self) -> None:
        """Rebuild the gallery from the current Theme state.

        Skipped when the active theme, favorites and themes_root all match the
        last rebuild's — __init__ and the boot-time load_from_config call this
        back to back with identical state, and without the guard the gallery
        gets built twice for nothing. A real change to any of the three still
        rebuilds.
        """
        state = self._gallery_state()
        if state == self._populated_state:
            return
        self._populated_state = state
        self.gallery.refresh()
        # One call covers populate, Revert and load_from_config: the latter two
        # both rebuild through here.
        self._refresh_contrast_warning()

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

    def _on_theme_activated(self, key: str) -> None:
        """Live-preview the activated theme."""
        if key != Theme.get_current_mode():
            Theme.set_mode(key)
            self._apply_to_app(key)
            # No full rebuild here: the only visible mutation is the Active
            # marker and the selection ring moving between two cards, and the
            # gallery already moved both when it emitted. _populated_state is
            # still updated to match — skipping that would leave it pointing at
            # the pre-click theme, so a later revert back to exactly that theme
            # would find the _populate() guard's state unchanged and wrongly
            # skip the rebuild that re-syncs the gallery's Active marker.
            self._populated_state = self._gallery_state()
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

    def _toggle_favorite(self, key: str) -> None:
        """Star/unstar `key`, refresh the affected card, notify listeners."""
        if Theme.is_favorite(key):
            Theme.remove_favorite(key)
        else:
            Theme.add_favorite(key)
        self.gallery.refresh_favorite(key)
        # No full rebuild — surgical star update. _populated_state still moves
        # to match (see _on_theme_activated): otherwise it points at the
        # pre-toggle favorites, and a later _populate() call that lands back on
        # that same stale tuple (e.g. a profile switch reseeding matching
        # favorites) wrongly skips the rebuild that would clear this star.
        self._populated_state = self._gallery_state()
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
        for key in keys:
            self.gallery.refresh_favorite(key)
        # See _toggle_favorite: surgical update, so _populated_state must move
        # too or it goes stale against the new favorites.
        self._populated_state = self._gallery_state()
        self.state_changed.emit(Theme.get_current_mode(), Theme.get_favorites())
        self.favorites_changed.emit()

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
        intent again so a later ordinary quit does not silently relaunch — but a
        *deferred* close is not a refusal, see :func:`_window_is_shutting_down`.
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
        if window is not None and not window.close() and not _window_is_shutting_down(window):
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
        the native-dialogs checkbox and the theme gallery keep showing the
        previous config's values and the user's next edit starts from a stale
        baseline.

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
        themes_root_changed = config.themes_root != self._themes_root
        self._themes_root = config.themes_root
        self.open_folder_btn.setToolTip(self._themes_folder_tooltip())

        # A profile switch re-runs Theme.initialize against a new themes_root,
        # and a user JSON file there can shadow a shipped theme under the same
        # key -- so a cached pixmap for that key may no longer be what the key
        # means. Scoped to an actual root change, not every reload: this method
        # also fires for wholly unrelated fields (this panel reloads on ANY
        # non-external field, e.g. "Use system file dialogs" — see this
        # docstring above), and those must not discard every cached thumbnail
        # and force a full re-render of every visible card.
        if themes_root_changed:
            clear_thumbnail_cache()

        # Rebuild the gallery so the Active marker, favorites stars and
        # selection follow the re-seeded Theme. Unconditional: favorites (and,
        # for a whole-config swap, the entire Theme state) can move without
        # config.theme changing.
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
