"""Settings tab with category organization using extracted panels."""

import dataclasses
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Protocol, cast, runtime_checkable

from PyQt6.QtCore import QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QAbstractScrollArea,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from anki_miner.config import (
    AnkiMinerConfig,
    AudioSourceEntry,
    ChainEntry,
    FreqEntry,
    PitchSourceEntry,
    create_default_config,
)
from anki_miner.config.paths import ANKI_MINER_HOME
from anki_miner.gui.controllers.anki_probe_controller import AnkiProbeController
from anki_miner.gui.controllers.audio_pack_import_flow import AudioPackImportFlow
from anki_miner.gui.controllers.dictionary_import_flow import DictionaryImportFlow
from anki_miner.gui.controllers.frequency_import_flow import FrequencyImportFlow
from anki_miner.gui.controllers.import_flow_common import ReimportAllFlow
from anki_miner.gui.controllers.pitch_import_flow import PitchImportFlow
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils import file_dialogs
from anki_miner.gui.utils.config_commit import ConfigCommitError, ConfigCommitResult
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.utils.dialog_paths import resolve_start_dir
from anki_miner.gui.utils.qt_helpers import install_no_scroll_on_inputs
from anki_miner.gui.utils.run_off_thread import run_off_thread
from anki_miner.gui.widgets.base import (
    PageWidth,
    ScreenIssue,
    ScreenIssueHost,
    SettingAnchor,
    SettingAnchorHost,
    capped_page_column,
    configure_scrolled_page,
)
from anki_miner.gui.widgets.enhanced import ModernButton
from anki_miner.gui.widgets.panels import (
    AnkiSettingsPanel,
    AudioPackSettingsPanel,
    DictionarySettingsPanel,
    FilteringSettingsPanel,
    FrequencySettingsPanel,
    MediaSettingsPanel,
    PitchSettingsPanel,
    UISettingsPanel,
    YouTubeSettingsPanel,
)
from anki_miner.gui.widgets.panels.chain_settings_panel_base import ChainSettingsPanelBase
from anki_miner.gui.widgets.panels.subtitles_settings_panel import SubtitlesSettingsPanel
from anki_miner.gui.widgets.settings_search import (
    BREADCRUMB_SEPARATOR,
    SEARCH_HIT_MS,
    SettingSearchEntry,
    SettingSearchSource,
    SettingsSearchBox,
    build_entries,
    flash_search_hit,
)
from anki_miner.services.audio_packs.registry import AudioPackMeta, AudioPackRegistry
from anki_miner.services.expression_audio_fetcher import purge_miss_markers
from anki_miner.services.frequency.registry import FreqSourceMeta, FrequencySourceRegistry
from anki_miner.services.known_word_db import KnownWordDB
from anki_miner.services.pitch_accent.registry import PitchSourceMeta, PitchSourceRegistry
from anki_miner.services.subtitle_parser import compile_subtitle_regex_filter
from anki_miner.utils.i18n import tr_format

# Debounce for the Settings auto-save: a burst of edits coalesces into one
# commit this many ms after the last change. Long enough to not commit per
# keystroke, short enough that settings apply near-immediately.
_AUTOSAVE_DEBOUNCE_MS = 1000

# Width ceiling for the settings navigator, in multiples of its own rendered
# line height. Read through font metrics rather than written as a pixel constant
# so the rail tracks the UI text scale.
#
# Line height, NOT `averageCharWidth()`: that metric is an advance average taken
# over the face's own glyph repertoire, so it collapses on a Latin face and
# doubles on a CJK one — 11px on DejaVu Sans against 21px on Noto Sans CJK JP at
# the same pixel size. The rail was therefore 336px on a desktop that resolves
# `Sans Serif` to Noto and 176px on one that resolves it to DejaVu, for the same
# text at the same scale. `height()` tracks the point size and stays within a
# quarter of itself across those two faces.
_NAV_WIDTH_LINES = 11


@runtime_checkable
class _SavePathPanel(Protocol):
    """Structural interface for panels that participate in the Save round-trip.

    Implemented by :class:`AnkiSettingsPanel`, :class:`MediaSettingsPanel`,
    :class:`FilteringSettingsPanel`, and :class:`YouTubeSettingsPanel`.
    """

    def load_from_config(self, config: AnkiMinerConfig) -> None: ...

    def contribute(self, config: AnkiMinerConfig) -> AnkiMinerConfig: ...


class SettingsTab(ScreenIssueHost, SettingAnchorHost, QWidget):
    """Settings tab with category organization.

    Uses extracted panel components for cleaner architecture: one panel per
    destination, reached from a grouped navigator down the side (see
    :meth:`_build_navigator`). Every setting stays visible; nothing is hidden
    behind a Basic/Advanced disclosure.

    Signals:
        validation_requested: Emitted when validation should be triggered
        config_changed: Emitted when configuration is saved (passes new config)
        ytdlp_update_requested: Emitted when the YouTube panel's "Update yt-dlp
            now" button is clicked (manual, forced).
        asr_download_requested: Emitted when the Subtitles panel's "Download
            model" button is clicked. Carries the selected model name.
        alass_download_requested: Emitted when the Subtitles panel's "Download
            alass" button is clicked.
        cuda_pack_download_requested: Emitted when the Subtitles panel's
            "Download GPU acceleration" button is clicked.
        vad_pack_download_requested: Emitted when the Subtitles panel's
            "Download silence removal" button is clicked.
        vulkan_model_download_requested: Emitted when the Subtitles panel's
            "Download Vulkan model" button is clicked. Carries the selected
            acoustic model name.
        manage_profiles_requested: Emitted by the footer's "Settings Profiles…"
            button. The window opens the dialog, not this tab: a profile switch
            reloads every panel here from the incoming config.
    """

    #: A label beside its control; a wider window buys gutters, not longer inputs.
    PAGE_WIDTH = PageWidth.PAGE

    ANCHOR_NAMESPACE = "app"

    validation_requested = pyqtSignal()
    config_changed = pyqtSignal(object)  # Emits AnkiMinerConfig
    ytdlp_update_requested = pyqtSignal()
    asr_download_requested = pyqtSignal(str)  # Emits model name
    alass_download_requested = pyqtSignal()
    cuda_pack_download_requested = pyqtSignal()
    vad_pack_download_requested = pyqtSignal()
    vulkan_model_download_requested = pyqtSignal(str)  # Emits model name
    manage_profiles_requested = pyqtSignal()

    # Fields written OUTSIDE the Settings Save path (theme selector, update
    # banner, first-run flags).  An update_config call that touches ONLY these
    # fields must NOT reload the panel widgets — that would destroy unsaved edits
    # the user has made in the Settings tab (OVH-007).
    _EXTERNAL_ONLY_FIELDS: frozenset[str] = frozenset(
        {
            "theme",
            "theme_favorites",
            "ui_font_scale",
            "ui_language",
            "skipped_update_version",
            "last_known_version",
            "first_run_shortcut_done",
            "first_run_setup_done",
            "config_version",
        }
    )

    # UI-appearance fields kept by Reset to Defaults (Issue #99) IN ADDITION to
    # GUIConfigManager.machine_specific_fields().  These are portable (so not
    # machine-specific), but they are applied live by the UI panel / Theme
    # singleton, not repainted by _load_config — resetting them would leave the
    # running session's theme/font unchanged yet persist defaults, silently
    # wiping the user's theme on the next launch.  Preserving them (as
    # _on_import_settings already does) keeps Reset safe and predictable.
    _RESET_PRESERVE_UI: frozenset[str] = frozenset(
        {"theme", "theme_favorites", "ui_font_scale", "ui_zoom", "ui_language"}
    )

    def __init__(
        self,
        config: AnkiMinerConfig,
        parent: QWidget | None = None,
        *,
        commit_config: Callable[[AnkiMinerConfig], None] | None = None,
        suppress_optional_startup: bool = False,
    ) -> None:
        """Initialize the settings tab.

        Args:
            config: Current configuration
            parent: Optional parent widget
            commit_config: Synchronous config commit used by import flows.
                Defaults to ``config_changed.emit`` for standalone tabs.
        """
        super().__init__(parent)
        self.config = config
        self._suppress_optional_startup = suppress_optional_startup
        self._commit_config: Callable[[AnkiMinerConfig], None] = (
            commit_config if commit_config is not None else self.config_changed.emit
        )
        # True between a manual "Update yt-dlp now" click and its result, so the
        # shared result signal can surface a dialog on the manual path only.
        self._ytdlp_manual_pending = False
        # Auto-save guards. _loading suppresses edit signals fired by
        # programmatic widget repopulation (_load_config, selector re-syncs);
        # _committing suppresses re-entry when the debounce fires while a
        # commit is already in flight.
        self._loading = False
        self._committing = False
        self._settings_dirty = False
        # One-shot lazy fetch of the deck / note-type dropdown contents. At
        # construction it would put a 15 s-timeout AnkiConnect call on the
        # startup path for users who never open Settings; on every show it
        # would re-hit Anki each visit. Same pattern as BackfillTab.showEvent.
        self._names_requested = False
        # Settings search (D11). The index is built at the END of construction,
        # not here: anchors resolve their text lazily and must be read after the
        # translators are installed, or the index is English for everyone.
        self._search_entries: dict[str, SettingSearchEntry] = {}
        self._pending_search_jump: SettingSearchEntry | None = None
        #: Test seam — zero clears the jump mark on the next event-loop turn.
        self._search_hit_ms = SEARCH_HIT_MS
        self._setup_ui()
        for panel in (self.dictionary_panel, self.audio_panel, self.frequency_panel, self.pitch_panel):
            panel.set_mutation_preflight(self.commit_pending_settings_for_mutation)
        self.dictionary_panel.set_remove_chain_commit(self._commit_dictionary_removal)
        self.audio_panel.set_remove_chain_commit(self._commit_audio_removal)
        self.frequency_panel.set_remove_chain_commit(self._commit_frequency_removal)
        self.pitch_panel.set_remove_chain_commit(self._commit_pitch_removal)
        # Controllers (T-66) own worker lifecycles + dialogs; the tab keeps
        # widgets, signal wiring, and config assembly. Dependency is one-way:
        # tab → controller → workers/services (tab-owned collaboration points
        # are injected as callables).
        # Dictionary add/reimport orchestration, incl. the Reimport-All
        # chained state machine and its predecessor deferral (T-09).
        self._dict_import_flow = DictionaryImportFlow(
            parent=self,
            panel=self.dictionary_panel,
            get_config=lambda: self.config,
            persist_chain=self._persist_chain_change,
            notify_config_changed=lambda: self.config_changed.emit(self.config),
        )
        # Audio pack add/reimport orchestration.
        self._audio_pack_import_flow = AudioPackImportFlow(
            parent=self,
            panel=self.audio_panel,
            get_config=lambda: self.config,
            persist_chain=self._persist_audio_chain_change,
            notify_config_changed=lambda: self.config_changed.emit(self.config),
        )
        # Frequency source add/reimport orchestration.
        self._frequency_import_flow = FrequencyImportFlow(
            parent=self,
            panel=self.frequency_panel,
            get_config=lambda: self.config,
            persist_chain=self._persist_frequency_chain_change,
            notify_config_changed=lambda: self.config_changed.emit(self.config),
        )
        # Pitch source add/reimport orchestration.
        self._pitch_import_flow = PitchImportFlow(
            parent=self,
            panel=self.pitch_panel,
            get_config=lambda: self.config,
            persist_chain=self._persist_pitch_chain_change,
            notify_config_changed=lambda: self.config_changed.emit(self.config),
        )
        # AnkiConnect probe workers (fetch fields / fetch decks / styling);
        # their live handles surface through iter_close_workers (T-12).
        self._anki_probe = AnkiProbeController(
            parent=self,
            anki_panel=self.anki_panel,
            filtering_panel=self.filtering_panel,
            get_config=lambda: self.config,
        )
        # Ordered list of panels that participate in the Save round-trip.
        # _load_config calls load_from_config on each; commit_settings folds
        # contribute() over them.  Dictionary/audio chain panels and the UI
        # panel are intentionally excluded — they persist via their own signals.
        self._save_panels: list[_SavePathPanel] = [
            self.anki_panel,
            self.media_panel,
            self.filtering_panel,
            self.youtube_panel,
            self.subtitles_panel,
        ]
        self._connect_signals()
        self._load_config()

        # Debounced auto-save: any edit in the save-path panels restarts this
        # timer; expiry commits everything at once. Created and wired AFTER the
        # initial _load_config so construction can't arm it.
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(_AUTOSAVE_DEBOUNCE_MS)
        self._debounce_timer.timeout.connect(self._commit_settings)
        self._wire_edit_signals()

        # A search jump lands one turn late on purpose: the page it selects has
        # not been laid out yet when open_subtab returns, so scrolling the
        # control into view now would measure a geometry that no longer holds.
        self._search_jump_timer = QTimer(self)
        self._search_jump_timer.setSingleShot(True)
        self._search_jump_timer.timeout.connect(self._reveal_pending_setting)
        self.refresh_setting_search_index()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        # Inset on all four edges: the navigator is a side rail, so unlike the
        # old sub-tab bar nothing here has to sit flush under the top-level tab
        # bar. The bottom chrome (update checkbox + Export/Import row) and the
        # panel forms keep the same margins they always had.
        layout.setContentsMargins(SPACING.md, SPACING.sm, SPACING.md, SPACING.md)

        # One banner for the whole tab (D24). Export/import, the known-words
        # maintenance actions and the AnkiConnect deck probe all report here;
        # the panels that own a repair of their own (the four chain lists,
        # Appearance) carry their own nearer banner and are found first.
        self.install_issue_banner(layout)

        # Grouped navigator (D10). Ten equally-weighted tabs carried no
        # hierarchy, and in a long locale at a large text size the strip
        # overflowed into scroll arrows that put whole categories out of reach.
        # A vertical list grows downward and scrolls a row into view on demand,
        # so every destination stays addressable at any width.
        self.nav_list = QListWidget()
        self.pages = QStackedWidget()

        # Create panels using extracted components
        self.anki_panel = AnkiSettingsPanel()
        self.media_panel = MediaSettingsPanel()
        self.dictionary_panel = DictionarySettingsPanel(self.config.dicts_root)
        self.audio_panel = AudioPackSettingsPanel(self.config.audio_packs_root)
        self.frequency_panel = FrequencySettingsPanel(self.config.freqs_root)
        self.pitch_panel = PitchSettingsPanel(self.config.pitch_root)
        self.filtering_panel = FilteringSettingsPanel()
        self.youtube_panel = YouTubeSettingsPanel()
        self.subtitles_panel = SubtitlesSettingsPanel(suppress_optional_startup=self._suppress_optional_startup)
        self.ui_panel = UISettingsPanel(
            self.config.themes_root,
            self.config.ui_zoom,
            self.config.ui_language,
            self.config.use_native_file_dialogs,
            self.config.ui_font_scale,
        )

        self._build_navigator()

        # Search the settings by name (D11). Above the navigator because it
        # addresses every destination, not the one currently open. It reveals
        # nothing that was hidden — every setting is on its page already — it
        # only saves the walk to the right page.
        self.search_box = SettingsSearchBox()
        self.search_box.setting_activated.connect(self.jump_to_setting)
        self.ignore_setting_widget(self.search_box, "finds settings; is not one itself")
        layout.addWidget(self.search_box)

        # Retained: _on_settings_subtab_changed and open_ui_subtab key off the
        # UI page; reading it from the map keeps a single source of truth.
        self._ui_subtab_index = self._subtab_index["ui"]
        # Reset the theme preview baseline when the user navigates away from
        # Appearance & Language so a later visit reverts to their last-chosen
        # theme, not session start. Connected after the navigator is populated,
        # so selecting the first destination can't fire it during construction.
        self.pages.currentChanged.connect(self._on_settings_subtab_changed)

        body = QHBoxLayout()
        body.setSpacing(SPACING.md)
        body.addWidget(self.nav_list)
        body.addWidget(self.pages, 1)
        layout.addLayout(body)

        # Updates row — single top-level toggle, no panel needed for one checkbox.
        self.check_for_updates_checkbox = QCheckBox(self.tr("Check for updates on startup"))
        self.check_for_updates_checkbox.setToolTip(
            self.tr("When enabled, Anki Miner queries GitHub for new releases on launch.")
        )
        layout.addWidget(self.check_for_updates_checkbox)
        # Lives on the tab, not in a panel, so it anchors here (D11).
        self.register_setting(
            "check_for_updates",
            self.check_for_updates_checkbox,
            lambda: (
                self.check_for_updates_checkbox.text(),
                self.check_for_updates_checkbox.toolTip(),
            ),
        )

        # Status row at bottom. The Save Settings button is gone — settings
        # auto-save (debounced) — but its inline "✓ Saved" confirmation stays
        # so each auto-commit is still visible.
        #
        # Reset to Defaults (Issue #99) is back but deliberately hard to
        # mis-fire: it sits far-left, separated from Export/Import by a wide
        # stretch; its confirm defaults to No; it has no keyboard shortcut; and
        # it preserves installed resources + theme (see
        # _on_reset_to_defaults_clicked). The earlier version was removed
        # because a stray Ctrl+R wiped the whole config in one keystroke.
        button_layout = QHBoxLayout()
        button_layout.setSpacing(SPACING.sm)

        self.reset_settings_button = ModernButton(self.tr("Reset to Defaults…"), variant="secondary")
        self.reset_settings_button.setToolTip(
            self.tr(
                "Reset settings to defaults. Installed dictionaries, audio, frequency lists, and your theme are kept."
            )
        )
        self.reset_settings_button.clicked.connect(self._on_reset_to_defaults_clicked)
        button_layout.addWidget(self.reset_settings_button)

        button_layout.addStretch()

        # Settings Profiles sits with the other whole-config actions rather than
        # at the foot of Appearance & Language, where the theme gallery pushed it
        # below the fold and it read as a third theme button. This footer is
        # outside the panels' scroll area, so one button serves all ten pages.
        # Left of Export/Import because it is the same kind of action: a named
        # snapshot of every setting, kept in the app instead of in a file.
        self.manage_profiles_button = ModernButton(self.tr("Settings Profiles…"), variant="secondary")
        self.manage_profiles_button.setToolTip(
            self.tr("Keep several complete settings snapshots and switch between them.")
        )
        self.manage_profiles_button.clicked.connect(self._on_manage_profiles_clicked)
        button_layout.addWidget(self.manage_profiles_button)

        self.export_settings_button = ModernButton(self.tr("Export Settings…"), variant="secondary")
        self.export_settings_button.setToolTip(
            self.tr("Save a portable settings file (machine-specific paths and resources excluded).")
        )
        self.export_settings_button.clicked.connect(self._on_export_settings)
        button_layout.addWidget(self.export_settings_button)

        self.import_settings_button = ModernButton(self.tr("Import Settings…"), variant="secondary")
        self.import_settings_button.setToolTip(
            self.tr("Apply settings from an exported file; anything not in the file is kept.")
        )
        self.import_settings_button.clicked.connect(self._on_import_settings)
        button_layout.addWidget(self.import_settings_button)

        # Inline, non-modal save confirmation. Flashed by _flash_save_status()
        # and auto-cleared by a timer; validation warnings park here sticky.
        self.save_status_label = QLabel("")
        self.save_status_label.setObjectName("settings-save-status")
        button_layout.addWidget(self.save_status_label)

        self._save_status_timer = QTimer(self)
        self._save_status_timer.setSingleShot(True)
        self._save_status_timer.timeout.connect(lambda: self.save_status_label.setText(""))

        layout.addLayout(button_layout)

        # Cap the whole screen, not the panels inside it. Settings is the one
        # page with a side rail, so capping each panel at the page measure would
        # have made the tab rail-plus-a-column wide -- wider than every other
        # screen, which is the width jump this change exists to remove. The
        # per-panel caps below stay, they just stop binding: the viewport left
        # over after the rail is already narrower than they are.
        body_widget = QWidget()
        body_widget.setLayout(layout)
        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(capped_page_column(body_widget, self.PAGE_WIDTH))
        self.setLayout(outer)

    def _build_navigator(self) -> None:
        """Fill the navigator with five headings over ten destinations (D10).

        Populates ``self.nav_list`` and ``self.pages`` together and records the
        stable key → page index map in ``_subtab_index``, which callers
        (``MainWindow.reveal_capability``, the Usage Guide browser, the theme
        shortcut) address settings by. Display names are presentation only: no
        navigation path reads a row's text.

        Headings are inert rows — disabled and unselectable — so arrow-key
        navigation steps over them and no selection can land on one.
        """
        self.nav_list.setObjectName("settings-nav")
        # Wrap rather than elide: a long translated destination name has to stay
        # readable, and the rail is width-capped just below.
        self.nav_list.setWordWrap(True)
        self.nav_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav_list.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
        self.nav_list.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Expanding)
        self.nav_list.setFrameShape(QFrame.Shape.NoFrame)
        self.nav_list.ensurePolished()
        # Width is set by _fit_navigator() once the destinations exist: it is
        # measured from their rendered labels, which cannot be done here.
        self.ignore_setting_widget(
            self.nav_list,
            "navigation between settings pages, not a setting itself",
        )

        groups: tuple[tuple[str, tuple[tuple[str, str, QWidget], ...]], ...] = (
            (
                self.tr("Cards"),
                (
                    ("anki", self.tr("Cards & Anki"), self.anki_panel),
                    ("media", self.tr("Card Media"), self.media_panel),
                ),
            ),
            (
                self.tr("Resources"),
                (
                    ("dictionaries", self.tr("Dictionaries"), self.dictionary_panel),
                    ("audio", self.tr("Audio"), self.audio_panel),
                    ("frequency", self.tr("Frequency"), self.frequency_panel),
                    ("pitch", self.tr("Pitch Accent"), self.pitch_panel),
                ),
            ),
            (
                self.tr("Mining"),
                (("filtering", self.tr("Filtering"), self.filtering_panel),),
            ),
            (
                self.tr("Integrations"),
                (
                    ("youtube", self.tr("YouTube"), self.youtube_panel),
                    ("subtitles", self.tr("Transcription & Alignment"), self.subtitles_panel),
                ),
            ),
            (
                self.tr("App"),
                (("ui", self.tr("Appearance & Language"), self.ui_panel),),
            ),
        )

        heading_font = QFont(self.nav_list.font())
        heading_font.setBold(True)
        self._subtab_index: dict[str, int] = {}
        # Where each destination sits, as the user reads it. Search shows this
        # under a result so a stable key never has to be explained to anyone.
        self._page_breadcrumbs: dict[str, str] = {}
        first_destination_row: int | None = None
        for heading, destinations in groups:
            item = QListWidgetItem(heading)
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            item.setFont(heading_font)
            self.nav_list.addItem(item)
            for key, label, panel in destinations:
                row = QListWidgetItem(label)
                row.setData(Qt.ItemDataRole.UserRole, key)
                self.nav_list.addItem(row)
                self._subtab_index[key] = self.pages.addWidget(self._wrap_in_scroll_area(panel))
                self._page_breadcrumbs[key] = f"{heading}{BREADCRUMB_SEPARATOR}{label}"
                if first_destination_row is None:
                    first_destination_row = self.nav_list.count() - 1

        self.nav_list.currentItemChanged.connect(self._on_nav_item_changed)
        if first_destination_row is not None:
            self.nav_list.setCurrentRow(first_destination_row)
        self._fit_navigator()

    def _fit_navigator(self) -> None:
        """Size the rail to its destinations and wrap whatever does not fit.

        Two halves, and both are load-bearing:

        *Width* is the widest destination as the view itself would lay it out
        (``sizeHintForColumn``, which asks the delegate and so already carries
        the QSS item padding), capped at the line-height budget above. Sizing
        from the labels means an English build gets a rail no wider than it
        needs, instead of a fixed character budget that is arbitrary in both
        directions.

        *Wrapping* is what ``setWordWrap(True)`` alone does not buy. A
        ``QListView`` lays rows out at the delegate's unconstrained hint, so a
        translated name simply made its row wider than the rail — and with the
        horizontal scrollbar off (it is: the rail is navigation, not a
        scroller), the overflow was clipped rather than reachable. Giving each
        item an explicit hint bounded to the content width is what makes the
        delegate wrap onto a second line instead.

        The vertical scrollbar's extent is reserved unconditionally: whether it
        appears depends on the wrapped heights this method is still computing,
        and a row that is a few px narrower than the viewport costs nothing
        while a row wider than it is the defect.
        """
        nav = self.nav_list
        # Drop any hint a previous pass installed first. An item size hint
        # overrides the delegate's, so measuring without clearing would measure
        # the last result rather than the labels at the current font.
        for row in range(nav.count()):
            stale = nav.item(row)
            if stale is not None:
                stale.setSizeHint(QSize())

        metrics = nav.fontMetrics()
        frame = 2 * nav.frameWidth()
        style = nav.style()
        scrollbar = style.pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent, None, nav) if style else 0

        budget = metrics.height() * _NAV_WIDTH_LINES
        width = min(nav.sizeHintForColumn(0) + frame + scrollbar, budget)
        nav.setMaximumWidth(width)

        content = max(1, width - frame - scrollbar)
        for row in range(nav.count()):
            item = nav.item(row)
            if item is None:
                continue
            # Per item, not once: headings and destinations carry different
            # horizontal padding (the heading outdent in common.qss).
            hint = nav.sizeHintForIndex(nav.indexFromItem(item))
            padding = QSize(
                max(0, hint.width() - metrics.horizontalAdvance(item.text())),
                max(0, hint.height() - metrics.height()),
            )
            wrapped = metrics.boundingRect(
                QRect(0, 0, max(1, content - padding.width()), 0),
                Qt.TextFlag.TextWordWrap,
                item.text(),
            )
            item.setSizeHint(QSize(content, wrapped.height() + padding.height()))

    def _nav_item(self, key: str) -> QListWidgetItem | None:
        """The navigator row carrying ``key``, or ``None`` if there is none."""
        for row in range(self.nav_list.count()):
            item = self.nav_list.item(row)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == key:
                return item
        return None

    def _on_nav_item_changed(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        """Show the page belonging to the newly selected destination."""
        key = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        index = self._subtab_index.get(key) if key else None
        if index is not None:
            self.pages.setCurrentIndex(index)

    def _connect_signals(self) -> None:
        """Connect panel signals to tab handlers."""
        # Anki panel signals
        # Lambda, not `.connect(self._anki_probe.refresh_name_lists)`: connecting
        # the bound method captures the function object at connect time, so a
        # later patch.object(tab._anki_probe, "refresh_name_lists") does NOT
        # intercept it — the signal still calls the real method, which starts two
        # AnkiConnect QThreads and fails the test on the socket tripwire. Late
        # attribute lookup through a lambda is patchable.
        self.anki_panel.deck_sync_requested.connect(lambda: self._anki_probe.refresh_name_lists())
        self.anki_panel.notetype_sync_requested.connect(lambda: self._anki_probe.refresh_name_lists())
        self.anki_panel.test_connection_requested.connect(self.validation_requested.emit)
        self.anki_panel.fetch_fields_requested.connect(self._anki_probe.fetch_fields)

        # Dictionary panel signals — wire Add/Reimport to the import flow
        # controller, which owns the worker dialogs (T-66).
        self.dictionary_panel.add_dict_requested.connect(self._dict_import_flow.add_dict)
        self.dictionary_panel.reimport_jmdict_requested.connect(self._dict_import_flow.reimport_jmdict)
        self.dictionary_panel.reimport_dict_requested.connect(self._dict_import_flow.reimport_dict)
        self.dictionary_panel.reimport_all_requested.connect(self._dict_import_flow.reimport_all)
        self.dictionary_panel.rescan_requested.connect(self._dict_import_flow.restore_unlisted)
        # Persist chain immediately after reorder/toggle.
        # Use a NARROW persist of just the chain — NOT the full Save pipeline
        # (T-08): the commit pipeline has unrelated validation gates (bad
        # dicts_root, missing cookies file, invalid regex, pitch/freq import
        # failure), any of which would skip persisting a removal and leave the
        # deleted dict_id orphaned — the exact Issue #30 bug this wiring
        # prevents — while its success path silently commits every panel's
        # unsaved edits.
        self.dictionary_panel.chain_changed.connect(
            lambda: self._persist_chain_change(self.dictionary_panel.get_chain())
        )

        # Audio panel signals — wire Add/Reimport to the import flow controller.
        self.audio_panel.add_pack_requested.connect(self._audio_pack_import_flow.add_pack)
        self.audio_panel.add_android_db_requested.connect(self._audio_pack_import_flow.add_android_db)
        self.audio_panel.reimport_pack_requested.connect(self._audio_pack_import_flow.reimport_pack)
        self.audio_panel.restore_requested.connect(self._restore_audio_from_disk)
        # Persist chain immediately after reorder/toggle.
        self.audio_panel.chain_changed.connect(lambda: self._persist_audio_chain_change(self.audio_panel.get_chain()))
        self.audio_panel.retry_missing_audio_requested.connect(self._on_retry_missing_audio)
        # Sentence-TTS toggles persist immediately, like the chain above.
        self.audio_panel.reading_tts_changed.connect(self._persist_reading_tts_change)

        # Frequency panel signals — wire Add/Reimport to the import flow.
        self.frequency_panel.add_source_requested.connect(self._frequency_import_flow.add_source)
        self.frequency_panel.reimport_source_requested.connect(self._frequency_import_flow.reimport_source)
        self.frequency_panel.reimport_all_requested.connect(self._frequency_import_flow.reimport_all)
        self.frequency_panel.restore_requested.connect(self._restore_frequency_from_disk)
        # Persist chain immediately after reorder/toggle.
        self.frequency_panel.chain_changed.connect(
            lambda: self._persist_frequency_chain_change(self.frequency_panel.get_chain())
        )

        # Pitch panel signals — same wiring as frequency.
        self.pitch_panel.add_source_requested.connect(self._pitch_import_flow.add_source)
        self.pitch_panel.reimport_source_requested.connect(self._pitch_import_flow.reimport_source)
        self.pitch_panel.reimport_all_requested.connect(self._pitch_import_flow.reimport_all)
        self.pitch_panel.restore_requested.connect(self._restore_pitch_from_disk)
        self.pitch_panel.chain_changed.connect(lambda: self._persist_pitch_chain_change(self.pitch_panel.get_chain()))

        # Filtering panel: excluded-decks picker + known-words cache rebuild (Issue #38).
        self.filtering_panel.fetch_decks_requested.connect(self._anki_probe.fetch_decks)
        self.filtering_panel.rebuild_known_words_requested.connect(self._on_rebuild_known_words)
        self.filtering_panel.manage_known_words_requested.connect(self._on_manage_known_words)

        # UI panel persists immediately on any change (live-preview model).
        self.ui_panel.state_changed.connect(self._on_theme_state_changed)
        self.ui_panel.font_scale_changed.connect(self._on_font_scale_changed)
        self.ui_panel.zoom_changed.connect(self._on_zoom_changed)
        self.ui_panel.native_dialogs_changed.connect(self._on_native_dialogs_changed)
        self.ui_panel.language_changed.connect(self._on_language_changed)
        # YouTube panel: manual "Update yt-dlp now" → re-emit to MainWindow
        # (app.py routes it to background_tasks.start_ytdlp_update(force=True)).
        self.youtube_panel.update_ytdlp_requested.connect(self._on_ytdlp_update_clicked)

        # Subtitles panel: "Download model" / "Download alass" → re-emit to
        # MainWindow (or caller), which owns the background download workers.
        self.subtitles_panel.asr_download_requested.connect(self._on_asr_download_clicked)
        self.subtitles_panel.alass_download_requested.connect(self._on_alass_download_clicked)
        self.subtitles_panel.cuda_pack_download_requested.connect(self._on_cuda_pack_download_clicked)
        self.subtitles_panel.vad_pack_download_requested.connect(self._on_vad_pack_download_clicked)
        self.subtitles_panel.vulkan_model_download_requested.connect(self._on_vulkan_download_clicked)

    def _start_restore_scan(
        self,
        panel: ChainSettingsPanelBase,
        work: Callable[[], object],
        on_done: Callable[[object], None],
        error_summary: str,
    ) -> None:
        if not panel.prepare_for_mutation():
            return
        token = panel.hold_mutation("restore")

        def finish(result: object) -> None:
            try:
                on_done(result)
            finally:
                panel.release(token)

        def fail(message: str) -> None:
            try:
                panel.show_screen_issue(ScreenIssue(summary=error_summary, details=message))
            finally:
                panel.release(token)

        try:
            run_off_thread(self, work, finish, fail)
        except Exception as error:  # noqa: BLE001 - dispatch failure is shown inline
            fail(str(error))

    def _show_nothing_to_restore(self, body: str) -> None:
        """Report that a Restore from Disk scan found nothing to add.

        Restore only re-adds sources present on disk but absent from the chain;
        ``unlisted()`` further drops anything schema-stale. After a schema bump
        that makes an empty result the *normal* outcome for everyone, so a
        silent return read as a dead button (the v2.10.0 report). Says so
        instead, and names Reimport All, which is what repairs a stale index.
        Mirrors ``DictionaryImportFlow.restore_unlisted``, which always spoke.
        """
        QMessageBox.information(self, self.tr("Nothing to restore"), body)

    def _restore_audio_from_disk(self) -> None:
        scan_root = self.config.audio_packs_root
        scan_chain = self.audio_panel.get_chain()
        panel_config = replace(self.config, expression_audio_chain=scan_chain)

        def scan() -> object:
            registry = AudioPackRegistry(scan_root)
            registry.load()
            return registry.unlisted(panel_config)

        def apply(result: object) -> None:
            if self.config.audio_packs_root != scan_root or self.audio_panel.get_chain() != scan_chain:
                return
            packs = cast(list[AudioPackMeta], result)
            if not packs:
                self._show_nothing_to_restore(
                    self.tr(
                        "Every audio pack found in the storage folder is already listed.\n\n"
                        "A pack that stopped working after an app upgrade is repaired by "
                        "Re-import on its row, not by restoring it."
                    )
                )
                return
            chain = list(scan_chain)
            entries = [AudioSourceEntry(kind="pack", pack_id=pack.pack_id, enabled=True) for pack in packs]
            insert_at = next(
                (index for index, entry in enumerate(chain) if entry.kind == "jpod101" and entry.enabled),
                len(chain),
            )
            new_chain = tuple(chain[:insert_at] + entries + chain[insert_at:])
            try:
                self._persist_audio_chain_change(new_chain)
            except Exception as error:  # noqa: BLE001 - persistence boundary
                self.audio_panel.show_screen_issue(
                    ScreenIssue(summary=self.tr("The audio packs could not be restored."), details=str(error))
                )
                return
            self.audio_panel.set_chain(new_chain)
            self.audio_panel.refresh_registry()

        self._start_restore_scan(
            self.audio_panel,
            scan,
            apply,
            self.tr("Installed audio packs could not be checked."),
        )

    def _restore_frequency_from_disk(self) -> None:
        scan_root = self.config.freqs_root
        scan_chain = self.frequency_panel.get_chain()
        panel_config = replace(self.config, frequency_chain=scan_chain)

        def scan() -> object:
            registry = FrequencySourceRegistry(scan_root)
            registry.load()
            return registry.unlisted(panel_config)

        def apply(result: object) -> None:
            if self.config.freqs_root != scan_root or self.frequency_panel.get_chain() != scan_chain:
                return
            sources = cast(list[FreqSourceMeta], result)
            if not sources:
                self._show_nothing_to_restore(
                    self.tr(
                        "Every frequency source found in the storage folder is already listed.\n\n"
                        "A source that stopped working after an app upgrade is repaired by "
                        "Reimport All, not by restoring it."
                    )
                )
                return
            new_chain = (*scan_chain, *(FreqEntry(source.source_id) for source in sources))
            try:
                self._persist_frequency_chain_change(new_chain)
            except Exception as error:  # noqa: BLE001 - persistence boundary
                self.frequency_panel.show_screen_issue(
                    ScreenIssue(summary=self.tr("The frequency sources could not be restored."), details=str(error))
                )
                return
            self.frequency_panel.set_chain(new_chain)
            self.frequency_panel.refresh_registry()

        self._start_restore_scan(
            self.frequency_panel,
            scan,
            apply,
            self.tr("Installed frequency sources could not be checked."),
        )

    def _restore_pitch_from_disk(self) -> None:
        scan_root = self.config.pitch_root
        scan_chain = self.pitch_panel.get_chain()
        panel_config = replace(self.config, pitch_chain=scan_chain)

        def scan() -> object:
            registry = PitchSourceRegistry(scan_root)
            registry.load()
            return registry.unlisted(panel_config)

        def apply(result: object) -> None:
            if self.config.pitch_root != scan_root or self.pitch_panel.get_chain() != scan_chain:
                return
            sources = cast(list[PitchSourceMeta], result)
            if not sources:
                self._show_nothing_to_restore(
                    self.tr(
                        "Every pitch accent source found in the storage folder is already listed.\n\n"
                        "A source that stopped working after an app upgrade is repaired by "
                        "Reimport All, not by restoring it."
                    )
                )
                return
            new_chain = (*scan_chain, *(PitchSourceEntry(source.source_id) for source in sources))
            try:
                self._persist_pitch_chain_change(new_chain)
            except Exception as error:  # noqa: BLE001 - persistence boundary
                self.pitch_panel.show_screen_issue(
                    ScreenIssue(summary=self.tr("The pitch accent sources could not be restored."), details=str(error))
                )
                return
            self.pitch_panel.set_chain(new_chain)
            self.pitch_panel.refresh_registry()

        self._start_restore_scan(
            self.pitch_panel,
            scan,
            apply,
            self.tr("Installed pitch accent sources could not be checked."),
        )

    def _wire_edit_signals(self) -> None:
        """Arm the auto-save debounce on any user edit in the save-path panels.

        Uses recursive ``findChildren`` — load-bearing: the FileSelectors
        embedded in the Filtering/YouTube panels (blacklist, whitelist,
        cookies) expose edits only through their NESTED QLineEdit; a
        direct-children walk would silently never auto-save those fields.
        Redundant arming (e.g. a spinbox's inner line edit) is harmless — the
        slot just restarts the timer. Programmatic repopulation is filtered by
        the ``_loading`` guard, not here.
        """
        from PyQt6.QtWidgets import QComboBox, QDoubleSpinBox, QLineEdit, QListWidget, QSpinBox

        panels: tuple[QWidget, ...] = (
            self.anki_panel,
            self.media_panel,
            self.filtering_panel,
            self.youtube_panel,
            self.subtitles_panel,
        )
        for panel in panels:
            for line_edit in panel.findChildren(QLineEdit):
                line_edit.textChanged.connect(self._on_settings_edited)
            for checkbox in panel.findChildren(QCheckBox):
                checkbox.toggled.connect(self._on_settings_edited)
            for spinbox in panel.findChildren(QSpinBox):
                spinbox.valueChanged.connect(self._on_settings_edited)
            for double_spinbox in panel.findChildren(QDoubleSpinBox):
                double_spinbox.valueChanged.connect(self._on_settings_edited)
            for combo in panel.findChildren(QComboBox):
                combo.currentIndexChanged.connect(self._on_settings_edited)
            for list_widget in panel.findChildren(QListWidget):
                # Excluded-decks list mutates via Add/Remove buttons, so the
                # widget itself has no edit signal — watch its model instead.
                model = list_widget.model()
                if model is not None:
                    model.rowsInserted.connect(self._on_settings_edited)
                    model.rowsRemoved.connect(self._on_settings_edited)

        # Fields outside the save panels that commit through the same path.
        self.check_for_updates_checkbox.toggled.connect(self._on_settings_edited)
        self.dictionary_panel.dicts_root_selector.path_changed.connect(self._on_settings_edited)

    def _on_settings_edited(self, *_args) -> None:
        """Restart the auto-save debounce on a user edit (no-op while loading)."""
        if self._loading:
            return
        self._settings_dirty = True
        self._debounce_timer.start()

    def _on_ytdlp_update_clicked(self) -> None:
        """Mark the next yt-dlp result as user-initiated, then request the update.

        The manual path may surface a message box on failure; the auto (startup)
        path must stay silent. The flag distinguishes them on the shared result
        signal — see :meth:`set_ytdlp_status_from_result`.
        """
        self._ytdlp_manual_pending = True
        self.youtube_panel.set_ytdlp_status(self.tr("Updating yt-dlp…"))
        self.ytdlp_update_requested.emit()

    def _on_asr_download_clicked(self, model_name: str) -> None:
        """Set a pending status and re-emit so the caller can start the download.

        The wiring (SettingsTab → caller → InstallWorker) mirrors the
        ytdlp_update_requested pattern: the tab updates its own status label and
        re-emits; the download itself is owned by the caller (MainWindow /
        background_tasks).
        """
        self.subtitles_panel.set_model_status(self.tr("Downloading…"))
        self.asr_download_requested.emit(model_name)

    def _on_alass_download_clicked(self) -> None:
        """Set a pending status and re-emit so the caller can start the download.

        Mirrors :meth:`_on_asr_download_clicked`: the download itself is owned by
        the caller (MainWindow / background_tasks).
        """
        self.subtitles_panel.set_alass_status(self.tr("Downloading…"))
        self.alass_download_requested.emit()

    def _on_cuda_pack_download_clicked(self) -> None:
        """Set a pending status and re-emit so the caller can start the download.

        Mirrors :meth:`_on_alass_download_clicked`: the download itself is owned
        by the caller (MainWindow / background_tasks).
        """
        self.subtitles_panel.set_cuda_pack_status(self.tr("Downloading…"))
        self.cuda_pack_download_requested.emit()

    def _on_vad_pack_download_clicked(self) -> None:
        """Set a pending status and re-emit so the caller can start the download.

        Mirrors :meth:`_on_cuda_pack_download_clicked`: the download itself is
        owned by the caller (MainWindow / background_tasks).
        """
        self.subtitles_panel.set_vad_pack_status(self.tr("Downloading…"))
        self.vad_pack_download_requested.emit()

    def _on_vulkan_download_clicked(self, model_name: str) -> None:
        """Set a pending status and re-emit so the caller can start the download.

        Mirrors :meth:`_on_vad_pack_download_clicked`: the download itself is
        owned by the caller (MainWindow / background_tasks). Carries the selected
        acoustic model name through to the wiring.
        """
        self.subtitles_panel.set_vulkan_status(self.tr("Downloading…"))
        self.vulkan_model_download_requested.emit(model_name)

    def set_asr_model_status(self, text: str) -> None:
        """Forward an ASR model download status line to the Subtitles panel."""
        self.subtitles_panel.set_model_status(text)

    def set_alass_status(self, text: str) -> None:
        """Forward an alass download status line to the Subtitles panel."""
        self.subtitles_panel.set_alass_status(text)

    def set_cuda_pack_status(self, text: str) -> None:
        """Forward a GPU-pack download status line to the Subtitles panel."""
        self.subtitles_panel.set_cuda_pack_status(text)

    def set_vad_pack_status(self, text: str) -> None:
        """Forward a VAD-pack download status line to the Subtitles panel."""
        self.subtitles_panel.set_vad_pack_status(text)

    def set_vulkan_status(self, text: str) -> None:
        """Forward a Vulkan model download status line to the Subtitles panel."""
        self.subtitles_panel.set_vulkan_status(text)

    def set_ytdlp_status(self, text: str) -> None:
        """Forward a yt-dlp updater status line to the YouTube panel."""
        self.youtube_panel.set_ytdlp_status(text)

    def set_ytdlp_status_from_result(self, result: object) -> None:
        """Update the YouTube panel status from a yt-dlp update result.

        Always refreshes the status line. On a user-initiated (manual) trigger,
        also pops a warning dialog for ``failed`` / ``unavailable``; the auto
        startup path stays silent (no-nag).
        """
        message = getattr(result, "message", "") or ""
        action = getattr(result, "action", "")
        self.youtube_panel.set_ytdlp_status(message)

        manual = getattr(self, "_ytdlp_manual_pending", False)
        self._ytdlp_manual_pending = False
        if manual and action in ("failed", "unavailable"):
            self.show_screen_issue(
                ScreenIssue(
                    summary=self.tr("yt-dlp could not be updated. Check your connection and try again."),
                    details=message,
                )
            )

    def _wrap_in_scroll_area(self, widget: QWidget) -> QScrollArea:
        """Wrap a widget in a scrollable container.

        Args:
            widget: Widget to wrap

        Returns:
            QScrollArea containing the widget
        """
        scroll_area = QScrollArea()
        configure_scrolled_page(scroll_area, widget, self.PAGE_WIDTH)
        # Issue #99: keep hover-scroll from mutating spin/combo values in the panel.
        install_no_scroll_on_inputs(widget)
        return scroll_area

    def showEvent(self, a0) -> None:  # noqa: N802 - Qt override
        """Fetch the deck / note-type lists the first time Settings is shown.

        Fires whenever the tab becomes VISIBLE — including from a tab switch on
        an already-visible window, not just an explicit ``show()``. Any test
        that makes this tab visible must stub ``refresh_name_lists`` or it will
        open a real AnkiConnect socket and trip the network guard.
        """
        super().showEvent(a0)
        # Again here, not only at construction: the rail is measured from its
        # own font metrics, and a widget built before it is polished can be
        # carrying the application default rather than the themed face.
        self._fit_navigator()
        if not self._names_requested:
            self._names_requested = True
            self._anki_probe.refresh_name_lists()

    def _load_config(self) -> None:
        """Load current configuration into UI.

        Save-path panels (Anki, Media, Filtering, YouTube) are loaded via the
        symmetric ``load_from_config`` contract so each panel owns its fields
        in one place (OVH-019).  Dictionary/audio chain panels and the
        top-level update checkbox persist via their own paths and are handled
        directly here.

        Runs under the ``_loading`` guard: the setText/setChecked/setValue
        calls below fire the same change signals user edits do, and must not
        arm the auto-save debounce (a reload would otherwise commit itself).
        """
        self._loading = True
        try:
            # Save-path panels — each owns its field list.
            for panel in self._save_panels:
                panel.load_from_config(self.config)

            # Dictionary chain (not part of the Save round-trip — persisted
            # immediately via chain_changed / _persist_chain_change).
            self.dictionary_panel.set_dicts_root(self.config.dicts_root)
            self.dictionary_panel.set_chain(self.config.dictionary_chain)

            # Audio source chain (same — immediate persist via its own signal).
            # The root goes first so the chain renders against the current root
            # and only one rescan is triggered.
            self.audio_panel.set_packs_root(self.config.audio_packs_root)
            self.audio_panel.set_chain(self.config.expression_audio_chain)
            self.audio_panel.set_reading_tts(
                self.config.reading_tts_enabled,
                self.config.reading_tts_google_enabled,
                self.config.reading_tts_papago_enabled,
            )

            # Frequency source chain lives in the Frequency tab; the chain persists
            # immediately via its own signal. Frequency activation is derived from an
            # enabled source being present (config.frequency_active) — no toggle. The
            # max-rank threshold is owned by filtering_panel and already loaded above.
            self.frequency_panel.set_freqs_root(self.config.freqs_root)
            self.frequency_panel.set_chain(self.config.frequency_chain)

            # Pitch source chain (same — immediate persist via its own signal).
            # Activation is derived from an enabled source (config.pitch_active).
            self.pitch_panel.set_pitch_root(self.config.pitch_root)
            self.pitch_panel.set_chain(self.config.pitch_chain)

            # Update settings — standalone checkbox outside all panels.
            self.check_for_updates_checkbox.setChecked(self.config.check_for_updates)

            # UI panel is outside _save_panels (it persists via its own signals),
            # so it owns its whole repaint here — signal-safe by construction.
            self.ui_panel.load_from_config(self.config)
        finally:
            self._loading = False

    def open_subtab(self, key: str) -> None:
        """Switch the settings navigator to the destination named by ``key``.

        ``key`` is a stable identifier from
        :data:`anki_miner.gui.capabilities.SETTINGS_SUBTABS` (e.g. ``"filtering"``,
        ``"anki"``). Unknown keys are ignored so a stale caller can't crash the UI.

        Moves the *selection*, which drives the page through
        :meth:`_on_nav_item_changed`, and scrolls the row into view so a deep
        link lands somewhere the user can see they arrived.
        """
        item = self._nav_item(key)
        if item is not None:
            self.nav_list.setCurrentItem(item)
            self.nav_list.scrollToItem(item)

    def current_subtab_key(self) -> str | None:
        """The stable key of the destination on show, or ``None``.

        The inverse of :meth:`open_subtab`, used to persist where the user was
        (D7). Read off the selected navigator row's ``UserRole`` data, never its
        displayed text, which moves with the UI language.
        """
        item = self.nav_list.currentItem()
        key = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return key if isinstance(key, str) and key else None

    def setting_anchor_hosts(self) -> tuple[SettingAnchorHost, ...]:
        """Every panel that registers setting anchors, in navigator order."""
        return (
            self.anki_panel,
            self.media_panel,
            self.dictionary_panel,
            self.audio_panel,
            self.frequency_panel,
            self.pitch_panel,
            self.filtering_panel,
            self.youtube_panel,
            self.subtitles_panel,
            self.ui_panel,
        )

    def setting_anchors(self) -> tuple[SettingAnchor, ...]:
        """Every addressable setting: this tab's own, then each panel's (D11).

        Collected on demand rather than at import, so a search index built by
        the caller sees whatever translator ``app.py`` installed.
        """
        anchors = list(super().setting_anchors())
        for host in self.setting_anchor_hosts():
            anchors.extend(host.setting_anchors())
        return tuple(anchors)

    def setting_ignore_reasons(self) -> Mapping[QWidget, str]:
        """Widgets across Settings deliberately excluded from anchoring.

        Aggregated like :meth:`setting_anchors`, so the two views never disagree
        about which surfaces were consulted.
        """
        reasons: dict[QWidget, str] = dict(super().setting_ignore_reasons())
        for host in self.setting_anchor_hosts():
            reasons.update(host.setting_ignore_reasons())
        return reasons

    # ------------------------------------------------------------------
    # Settings search (D11)
    # ------------------------------------------------------------------

    def setting_search_sources(self) -> tuple[SettingSearchSource, ...]:
        """Every page's anchors paired with the breadcrumb they live under.

        A panel's ``ANCHOR_NAMESPACE`` is its navigator key, which is what makes
        an anchor id self-locating: ``filtering.max_frequency_spinbox`` names
        both the page to open and the control to focus. This tab's own anchors
        get no page — they sit below the navigator and are always on screen.
        """
        sources = [
            SettingSearchSource(
                page_key="",
                breadcrumb=self.tr("Settings"),
                anchors=super().setting_anchors(),
            )
        ]
        for host in self.setting_anchor_hosts():
            key = host.ANCHOR_NAMESPACE
            sources.append(
                SettingSearchSource(
                    page_key=key,
                    breadcrumb=self._page_breadcrumbs[key],
                    anchors=host.setting_anchors(),
                )
            )
        return tuple(sources)

    def setting_search_entries(self) -> tuple[SettingSearchEntry, ...]:
        """The current search index, in navigator order."""
        return tuple(self._search_entries.values())

    def refresh_setting_search_index(self) -> None:
        """Rebuild the search index from the anchors registered right now.

        Called once at the end of construction, when the translators are in
        place. Call it again after registering or dropping anchors; nothing
        rebuilds it implicitly, because nothing else knows when the set changed.
        """
        entries = build_entries(self.setting_search_sources())
        self._search_entries = {entry.stable_id: entry for entry in entries}
        self.search_box.set_entries(entries)

    def jump_to_setting(self, stable_id: str) -> None:
        """Open the page holding ``stable_id`` and reveal that exact control.

        Unknown ids are ignored so a stale deep link cannot crash the UI. The
        reveal itself is deferred one turn — see ``_search_jump_timer``.
        """
        entry = self._search_entries.get(stable_id)
        if entry is None:
            return
        if entry.page_key:
            self.open_subtab(entry.page_key)
        self._pending_search_jump = entry
        self._search_jump_timer.start(0)

    def _reveal_pending_setting(self) -> None:
        """Scroll to, focus, and mark the control a jump selected."""
        entry = self._pending_search_jump
        self._pending_search_jump = None
        if entry is None:
            return
        anchor = entry.anchor
        page = self.pages.widget(self._subtab_index[entry.page_key]) if entry.page_key else None
        if isinstance(page, QScrollArea):
            page.ensureWidgetVisible(anchor.scroll_widget)
        anchor.focus_widget.setFocus(Qt.FocusReason.ShortcutFocusReason)
        flash_search_hit(anchor.highlight_widget, duration_ms=self._search_hit_ms)

    def trigger_reimport_all(
        self,
        only_ids: frozenset[str] | None = None,
        *,
        kind: str = "dictionary",
        on_complete: Callable[[], None] | None = None,
    ) -> None:
        """Run one family's Reimport All flow (4.0 migration prompt hook).

        Public entry point the startup schema-staleness prompt calls after the
        user opts to reimport now. ``only_ids`` keeps that repair scoped to the
        stale slots found by the startup scan; ``None`` preserves manual
        Reimport All behavior. ``kind`` selects the family, and ``on_complete``
        lets the prompt chain the next one when this batch finishes.

        Delegates to the same ``reimport_all`` each panel button drives, so the
        one-click migration and the manual path share one implementation.
        """
        flows: dict[str, tuple[str, ReimportAllFlow]] = {
            "dictionary": ("dictionaries", self._dict_import_flow),
            "frequency": ("frequency", self._frequency_import_flow),
            "pitch": ("pitch", self._pitch_import_flow),
        }
        subtab, flow = flows[kind]
        self.open_subtab(subtab)
        flow.reimport_all(only_ids=only_ids, on_complete=on_complete)

    def set_dictionary_mutation_preflight(self, callback: Callable[[], bool] | None) -> None:
        """Install the startup-migration preflight for dictionary mutations."""
        self.dictionary_panel.set_external_mutation_preflight(callback)

    def open_ui_subtab(self) -> None:
        """Switch to Appearance & Language (language, zoom, text size, themes).

        Thin wrapper over :meth:`open_subtab` kept because MainWindow's
        ``_settings_tab_index`` uses this method name as the capability marker
        that identifies the Settings tab, and the 'All themes…' header sentinel
        calls it directly. The ``"ui"`` key is unchanged by the D10 rename.
        """
        self.open_subtab("ui")

    def _on_settings_subtab_changed(self, index: int) -> None:
        """Reset the UI panel's theme preview baseline when leaving its page."""
        if index != self._ui_subtab_index:
            self.ui_panel.reset_baseline()

    def _commit_immediate_config(
        self,
        config: AnkiMinerConfig,
        commit: Callable[[AnkiMinerConfig], None],
    ) -> None:
        """Mark a narrow immediate write so its synchronous echo is adoption-only."""
        was_committing = self._committing
        self._committing = True
        try:
            commit(config)
        finally:
            self._committing = was_committing

    def _on_theme_state_changed(self, active: str, favorites: tuple) -> None:
        """Forward Themes panel changes through ``config_changed``.

        The Themes panel writes through Theme directly (live preview); this
        slot mirrors the change into ``self.config`` and re-emits so the
        existing ``config_changed`` → ``MainWindow.update_config`` chain
        persists to ``gui_config.json`` without duplicate logic.
        """
        new_config = replace(self.config, theme=active, theme_favorites=tuple(favorites))
        self._commit_immediate_config(new_config, self.config_changed.emit)

    def _on_font_scale_changed(self, scale: float) -> None:
        """Fold the UI panel's text-size change into the config and persist.

        Text size is restart-to-apply (D39b-A), so unlike theme nothing is made
        live here: the panel reveals its restart note and this slot only folds
        ``ui_font_scale`` into the config so the existing ``config_changed`` →
        ``MainWindow.update_config`` chain writes it to ``gui_config.json``.
        The running process keeps the boot scale until it is relaunched.
        """
        new_config = replace(self.config, ui_font_scale=scale)
        self._commit_immediate_config(new_config, self.config_changed.emit)

    def _on_zoom_changed(self, zoom: float) -> None:
        """Persist a whole-UI zoom change immediately (applies on next launch).

        Zoom is injected as QT_SCALE_FACTOR before QApplication is built, so
        unlike font scale there is no live restyle — the Themes panel reveals a
        restart note and this slot only folds ``ui_zoom`` into the config.
        """
        new_config = replace(self.config, ui_zoom=zoom)
        self._commit_immediate_config(new_config, self.config_changed.emit)

    def _on_language_changed(self, language: str) -> None:
        """Persist a UI-language change immediately (applies on next launch)."""
        new_config = replace(self.config, ui_language=language)
        self._commit_immediate_config(new_config, self.config_changed.emit)

    def _on_native_dialogs_changed(self, use_native: bool) -> None:
        """Persist the file-dialog mode immediately (applies to the next dialog).

        The live module state is re-seeded by ``MainWindow.update_config`` on
        the committed config, so no direct ``file_dialogs`` call here.
        """
        new_config = replace(self.config, use_native_file_dialogs=use_native)
        self._commit_immediate_config(new_config, self.config_changed.emit)

    def commit_settings(self) -> None:
        """Commit the save-path panels into the config and emit ``config_changed``.

        Public entry point for tests and the close flush; the auto-save
        debounce timer drives :meth:`_commit_settings` directly.
        """
        self._commit_settings()

    def flush_pending_settings(self) -> None:
        """Commit a pending debounced edit immediately (close-time flush).

        MainWindow.closeEvent calls this at its very TOP — before the
        background-task shutdown fan-out reaches :meth:`shutdown`, and before
        the deferred-close path returns without ever reaching the final save.
        Committing here routes through config_changed →
        MainWindow.update_config, which writes gui_config.json and updates
        MainWindow.config, so both close paths persist the edit.
        """
        if self._settings_dirty:
            self._debounce_timer.stop()
            self._commit_settings()

    def commit_pending_settings_for_mutation(self) -> bool:
        """Commit pending edits normally before a root-bound mutation.

        Refuse re-entry or an already-owned panel without consuming the
        debounce timer.
        """
        if self._committing or any(
            panel.has_active_mutation()
            for panel in (self.dictionary_panel, self.audio_panel, self.frequency_panel, self.pitch_panel)
        ):
            if self._settings_dirty:
                self._debounce_timer.start()
            return False
        if not self._settings_dirty:
            return True
        try:
            committed = self._commit_settings(commit_config=self._commit_config)
        except ConfigCommitError as error:
            if error.result.persisted:
                return False
            self._debounce_timer.start()
            return False
        except Exception:  # noqa: BLE001 - unknown commit phase must refuse the mutation
            self._debounce_timer.start()
            return False
        return committed and not self._settings_dirty

    def _commit_settings(
        self,
        *,
        commit_config: Callable[[AnkiMinerConfig], None] | None = None,
    ) -> bool:
        """Debounced auto-save commit with per-field validation.

        Unlike the old Save-button flow (modal warning + whole-save abort),
        an invalid field must NOT block unrelated edits: under silent
        auto-save a stale invalid value (deleted cookies.txt, bad regex —
        both gates fire even when untouched) would otherwise stop EVERY
        setting from persisting, with only a small label as evidence. Each
        failing field keeps its last-good value from ``self.config``, the
        rest commits, and a sticky inline warning names what was kept.
        """
        if self._committing or any(
            panel.has_active_mutation()
            for panel in (self.dictionary_panel, self.audio_panel, self.frequency_panel, self.pitch_panel)
        ):
            # The debounce fired while a commit is in flight, or while a panel
            # owns indexed-resource mutation authority — retry afterwards.
            if self._settings_dirty:
                self._debounce_timer.start()
            return False
        self._committing = True
        dirty_before_commit = self._settings_dirty
        try:
            # This commit consumes whatever edits armed the timer — cancel a
            # still-pending expiry so it can't fire a redundant second commit.
            self._debounce_timer.stop()
            self._settings_dirty = False
            self._commit_settings_locked(commit_config)
        except ConfigCommitError as error:
            if not error.result.persisted:
                self._settings_dirty = dirty_before_commit or self._settings_dirty
                if self._settings_dirty:
                    self._debounce_timer.start()
            raise
        except Exception:
            self._settings_dirty = dirty_before_commit or self._settings_dirty
            if self._settings_dirty:
                self._debounce_timer.start()
            raise
        finally:
            self._committing = False
        return True

    def _commit_settings_locked(
        self,
        commit_config: Callable[[AnkiMinerConfig], None] | None,
    ) -> None:
        # If the user just re-enabled startup checks (False -> True), clear any
        # previously skipped version so a fresh check runs next launch.
        was_enabled = self.config.check_for_updates
        now_enabled = self.check_for_updates_checkbox.isChecked()
        skipped_update_version = self.config.skipped_update_version
        if now_enabled and not was_enabled:
            skipped_update_version = ""

        # Human-readable names of fields whose edit was kept back (last-good
        # value re-used) because validation failed. Drives the sticky warning.
        kept_back: list[str] = []

        # Validate dictionary storage folder (Issue #45). Only enforced when
        # the user has changed the path — reuse-of-current always passes so a
        # transiently-unavailable mount (external SSD) doesn't block other
        # unrelated edits from saving.
        new_dicts_root = self.dictionary_panel.get_dicts_root()
        if new_dicts_root != self.config.dicts_root and (
            not new_dicts_root.is_dir() or not os.access(new_dicts_root, os.W_OK)
        ):
            kept_back.append(self.tr("dictionary folder (Dictionaries)"))
            new_dicts_root = self.config.dicts_root
        dicts_root_changed = new_dicts_root != self.config.dicts_root

        # Fold: each panel's contribute() returns a new frozen config with its
        # own fields applied.  Panels outside the Save round-trip (dictionary /
        # audio / frequency chain, themes) are handled separately below.
        new_config = self.config
        for panel in self._save_panels:
            new_config = panel.contribute(new_config)

        # Validate the YouTube cookies file (Issue #62). yt-dlp would otherwise
        # fail mid-fetch with a cryptic message; catch a bad path up front.
        # An empty field is valid (no cookies file).
        cookies_file = self.youtube_panel.get_cookies_file()
        if cookies_file and not Path(cookies_file).is_file():
            kept_back.append(self.tr("cookies file (YouTube)"))
            new_config = replace(new_config, youtube_cookies_file=self.config.youtube_cookies_file)

        # Validate subtitle regex filter before persistence, even while disabled.
        # The toggle, pattern AND replacement are kept back together — the
        # replacement is folded in by the filtering panel's contribute(), so
        # reverting only pattern+toggle would leave the last-good pattern paired
        # with a new replacement the user never previewed.
        subtitle_regex = self.filtering_panel.get_subtitle_regex_filter()
        subtitle_regex_replacement = self.filtering_panel.get_subtitle_regex_replacement()
        if subtitle_regex or subtitle_regex_replacement:
            try:
                compile_subtitle_regex_filter(subtitle_regex, subtitle_regex_replacement)
            except ValueError:
                kept_back.append(self.tr("subtitle regex (Filtering)"))
                new_config = replace(
                    new_config,
                    subtitle_regex_filter=self.config.subtitle_regex_filter,
                    subtitle_regex_replacement=self.config.subtitle_regex_replacement,
                    use_subtitle_regex_filter=self.config.use_subtitle_regex_filter,
                )

        new_config = replace(
            new_config,
            # Dictionary storage folder (Issue #45). Validated above; reuse of
            # current value passes through unchanged.
            dicts_root=new_dicts_root,
            # Sentence-TTS toggles — same immediate-persist + full-Save sync.
            reading_tts_enabled=self.audio_panel.get_reading_tts()[0],
            reading_tts_google_enabled=self.audio_panel.get_reading_tts()[1],
            reading_tts_papago_enabled=self.audio_panel.get_reading_tts()[2],
            # Update settings
            check_for_updates=now_enabled,
            skipped_update_version=skipped_update_version,
        )

        # Async import flows can complete between the start-of-Save snapshot
        # and this point. Re-read all immediate-persist chains only now so a
        # completion mid-Save cannot be overwritten by a stale snapshot.
        new_config = replace(
            new_config,
            dictionary_chain=self.dictionary_panel.get_chain(),
            frequency_chain=self.frequency_panel.get_chain(),
            expression_audio_chain=self.audio_panel.get_chain(),
            pitch_chain=self.pitch_panel.get_chain(),
        )

        # Emit signal to notify listeners of config change
        try:
            if commit_config is None:
                self.config_changed.emit(new_config)
            else:
                commit_config(new_config)
        except ConfigCommitError as error:
            if error.result.persisted:
                self._sync_persisted_config(new_config)
                if dicts_root_changed:
                    self._loading = True
                    try:
                        self.dictionary_panel.set_dicts_root(new_dicts_root)
                    finally:
                        self._loading = False
            raise

        # Sync the dictionary panel only after configuration persistence
        # succeeds, so a failed save cannot transfer mutation authority to an
        # uncommitted root. Under _loading because set_dicts_root re-emits the
        # selector's path_changed.
        if dicts_root_changed:
            self._loading = True
            try:
                self.dictionary_panel.set_dicts_root(new_dicts_root)
            finally:
                self._loading = False
        if kept_back:
            # Sticky (no auto-clear): stays visible until the next fully-valid
            # commit replaces it with the ✓ flash.
            self._save_status_timer.stop()
            self.save_status_label.setText(tr_format(self.tr("⚠ Saved — kept previous: %1"), ", ".join(kept_back)))
        else:
            self._flash_save_status(self.tr("✓ Saved"))

    def _on_manage_profiles_clicked(self) -> None:
        """Ask the window for the profile manager; never own the dialog here.

        A profile switch fans ``config_refreshed`` into every panel in this tab,
        so a dialog parented here would be repainted mid-CRUD. MainWindow owns it
        (see ``_open_profile_manager``).
        """
        self.manage_profiles_requested.emit()

    def _on_export_settings(self) -> None:
        """Export a portable settings file (machine-specific fields stripped)."""

        def _on_picked(target: str) -> None:
            if not target:
                return
            try:
                GUIConfigManager.export_config(self.config, Path(target))
            except OSError as e:
                # The path and the errno are what a bug report needs and what a
                # reader does not: Details, not the sentence (D24).
                self.show_screen_issue(
                    ScreenIssue(
                        summary=self.tr("Settings could not be exported."),
                        details=f"{target}: {e}",
                        action_id="settings.export-retry",
                        action_text=self.tr("Retry"),
                    ),
                    action=self._on_export_settings,
                )
                return
            self.clear_screen_issue()
            QMessageBox.information(
                self,
                self.tr("Settings Exported"),
                tr_format(self.tr("Portable settings written to %1."), target),
            )

        file_dialogs.pick_save_file(
            self,
            self.tr("Export Settings"),
            str(Path(resolve_start_dir(None, file_mode=True)) / "anki_miner_settings.json"),
            self.tr("JSON Files (*.json);;All Files (*)"),
            on_done=_on_picked,
        )

    def _on_import_settings(self) -> None:
        """Overlay a settings file onto the current config (confirm first).

        Values in the file override the current settings; anything missing
        from the file — including every machine-specific field the export
        strips — keeps its current value. Applies via the same
        ``config_changed`` path as a commit, then reloads every panel.
        """
        file_dialogs.pick_open_file(
            self,
            self.tr("Import Settings"),
            resolve_start_dir(None, file_mode=True),
            self.tr("JSON Files (*.json);;All Files (*)"),
            on_done=self._apply_settings_import,
        )

    def _apply_settings_import(self, source: str) -> None:
        """Confirm and apply a settings file chosen by ``_on_import_settings``.

        Split out of the picker slot because the picker is non-blocking now: the
        continuation is a callback, and this body is far too long to read as a
        closure.
        """
        if not source:
            return
        reply = QMessageBox.question(
            self,
            self.tr("Import Settings?"),
            tr_format(
                self.tr(
                    "Apply settings from %1?\n\n"
                    "Imported values override your current settings; anything "
                    "not in the file is kept."
                ),
                source,
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            import_result = GUIConfigManager.import_config(Path(source), self.config)
        except (json.JSONDecodeError, TypeError, ValueError, OSError) as e:
            self.show_screen_issue(
                ScreenIssue(
                    summary=self.tr("Settings could not be imported."),
                    details=f"{source}: {e}",
                    action_id="settings.import-retry",
                    action_text=self.tr("Retry"),
                ),
                action=self._on_import_settings,
            )
            return
        self.clear_screen_issue()
        new_config = import_result.config
        # Validate imported subtitle-regex semantics the same way the commit path does.
        # import_config validates the field types but does not compile the pattern, so
        # an invalid pattern would otherwise persist even while disabled. Reject
        # it here and warn the user, so the failure is surfaced rather than stored.
        if new_config.subtitle_regex_filter or new_config.subtitle_regex_replacement:
            try:
                compile_subtitle_regex_filter(new_config.subtitle_regex_filter, new_config.subtitle_regex_replacement)
            except ValueError as e:
                new_config = replace(
                    new_config,
                    subtitle_regex_filter=self.config.subtitle_regex_filter,
                    subtitle_regex_replacement=self.config.subtitle_regex_replacement,
                    use_subtitle_regex_filter=self.config.use_subtitle_regex_filter,
                )
                self.show_screen_issue(
                    ScreenIssue(
                        summary=self.tr(
                            "The imported subtitle regex filter was rejected; your previous filter was kept."
                        ),
                        details=str(e),
                    )
                )
        # Import can touch any field — full reload, unlike the targeted
        # auto-save commit.
        self.config_changed.emit(new_config)
        self._load_config()
        if import_result.invalid_fields or import_result.notices:
            summary: list[str] = []
            if import_result.invalid_fields:
                summary.append(
                    tr_format(
                        self.tr("Invalid imported fields were ignored; current values were kept: %1"),
                        ", ".join(import_result.invalid_fields),
                    )
                )
            for notice in import_result.notices:
                if notice == ("Auto-update of yt-dlp was disabled (settings imported from an older version)."):
                    summary.append(
                        self.tr("Auto-update of yt-dlp was disabled (settings imported from an older version).")
                    )
                elif notice == ("Settings from version 2.8.3 were mapped conservatively to schema 2."):
                    summary.append(self.tr("Settings from version 2.8.3 were mapped conservatively to schema 2."))
                else:
                    summary.append(notice)
            QMessageBox.information(
                self,
                self.tr("Settings Imported"),
                "\n\n".join(summary),
            )
        else:
            self._flash_save_status(self.tr("✓ Imported"))

    def _on_reset_to_defaults_clicked(self) -> None:
        """Reset settings to defaults after an explicit confirm (Issue #99).

        Deliberately safe: cancels any pending debounced edit first, confirms
        with No as the default button, and preserves machine-specific fields
        (installed dictionary/audio/frequency chains, paths, first-run state)
        plus the UI-appearance fields (theme/font/zoom/language) — so a reset
        can neither wipe installed resources nor silently drop the user's theme
        on the next launch. Only the behavioural settings that Issue #99's
        scroll-through can corrupt are returned to defaults.
        """
        if self._debounce_timer.isActive():
            # A pending edit would re-commit ~1s later and clobber the reset.
            self._debounce_timer.stop()
        reply = QMessageBox.question(
            self,
            self.tr("Reset Settings"),
            self.tr(
                "Reset all settings to their defaults?\n\n"
                "Your installed dictionaries, audio, frequency lists, and theme are kept."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,  # safe default focus / Enter target
        )
        if reply != QMessageBox.StandardButton.Yes:
            if self._settings_dirty:
                self._debounce_timer.start()
            return
        preserve = GUIConfigManager.machine_specific_fields() | self._RESET_PRESERVE_UI
        preserved = {name: getattr(self.config, name) for name in preserve}
        self.config = replace(create_default_config(), **preserved)
        self._load_config()  # repaint the reset panels (under the _loading guard)
        self._settings_dirty = False
        self.config_changed.emit(self.config)  # persist via MainWindow.update_config
        self._flash_save_status(self.tr("✓ Reset to defaults"))

    def _flash_save_status(self, text: str) -> None:
        """Show a transient, non-modal confirmation beside the Save button.

        Restarts the auto-clear timer on each call so repeated saves keep the
        message visible for the full duration.
        """
        self.save_status_label.setText(text)
        self._save_status_timer.stop()
        self._save_status_timer.start(2500)

    def update_config(self, config: AnkiMinerConfig) -> None:
        """Update configuration from external source.

        A synchronous echo from this tab's own commit is adoption-only: the
        widgets already contain the values that were just saved. External
        refreshes still follow the field-diff rules below.

        Skips reloading the panel widgets when the incoming config differs from
        the current one ONLY in externally-managed fields (theme, font scale,
        first-run flags, update-banner fields — see ``_EXTERNAL_ONLY_FIELDS``).
        This preserves unsaved edits the user has made in the Settings tab when,
        for example, a theme change arrives via config_refreshed (OVH-007).

        Genuinely panel-relevant changes (e.g. JMdict migration updates
        dicts_root) still trigger the full reload. A caller that means "adopt
        this whole config and redraw regardless" wants
        :meth:`reload_from_config` — the allowlist here is not a hint, and a
        settings-profile switch has to bypass it.

        Args:
            config: New configuration to load
        """
        if self._committing:
            self.config = config
            return
        changed = {
            f.name for f in dataclasses.fields(config) if getattr(config, f.name) != getattr(self.config, f.name)
        }
        self.config = config
        if not changed or changed <= self._EXTERNAL_ONLY_FIELDS:
            # No panel-relevant field changed: either identical config (no-op
            # refresh) or every diff is in the externally-managed allowlist.
            # Skip reload to preserve in-progress widget edits.
            return
        self._load_config()

    def reload_from_config(self, config: AnkiMinerConfig) -> None:
        """Adopt ``config`` and repaint every panel, allowlist or not.

        The explicit counterpart to :meth:`update_config`, whose
        ``_EXTERNAL_ONLY_FIELDS`` short-circuit exists to protect unsaved panel
        edits during unrelated commits (OVH-007) and must stay exactly as it is.
        A settings-profile switch is the case that gate gets wrong: two profiles
        differing only in theme / favorites / font scale / language produce a
        diff that lies ENTIRELY inside the allowlist (the version stamps ride in
        it too), so the panels would keep rendering the profile the user just
        left — a stale language and zoom combo, a hidden restart note, and a
        theme tree drawing the outgoing favorites while the ``Theme`` singleton
        already holds the incoming ones, so the next star click toggles the
        opposite of what is drawn.

        Callers must own the WHOLE config: this discards in-progress panel edits
        by design, which is why it is a separate entry point rather than a flag.

        The theme Revert baseline is re-pointed unconditionally afterwards.
        ``UISettingsPanel._load_config`` re-points it only when the incoming
        theme differs from the one the panel last made live — a guard that is
        right for an ordinary reload but wrong here: if the incoming profile's
        theme happens to equal the one being previewed, an external swap reads
        as the panel's own preview and the baseline stays on the OUTGOING
        profile's theme, so Revert then applies AND persists a theme the
        incoming profile never had. A whole-config adoption is by definition
        external, so there is nothing to protect.

        Args:
            config: Configuration to render; becomes ``self.config``.
        """
        self.config = config
        self._load_config()
        self.ui_panel.reset_baseline()

    def iter_close_workers(self) -> tuple:
        """Live worker handles MainWindow must join on close (T-12).

        Chains the four AnkiConnect probe workers (T-66) with the active
        import workers from all four import flows (OVH-004, 059, 060) so
        ``BackgroundTaskController._join_worker_for_close`` sees every live
        Settings-tab QThread.  ``None`` entries (idle flows) are filtered
        by ``_join_worker_for_close``.
        """
        return (
            *self._anki_probe.iter_close_workers(),
            *self._dict_import_flow.iter_close_workers(),
            *self._audio_pack_import_flow.iter_close_workers(),
            *self._frequency_import_flow.iter_close_workers(),
            *self._pitch_import_flow.iter_close_workers(),
        )

    def shutdown(self) -> None:
        """Cancel active import batches and AnkiConnect workers without waiting.

        Explicit-teardown entry point mirroring the YouTube tab. Import batch
        cancellation runs before the shared close policy enumerates and joins
        workers; probe cancellation delegates to :class:`AnkiProbeController`.

        Also stops the auto-save debounce so an armed timer can never fire
        into a torn-down widget (the pytest-qt ``_drain_qt_deletes`` segfault
        class). Pending edits are persisted by ``flush_pending_settings``,
        which MainWindow.closeEvent runs BEFORE the shutdown fan-out reaches
        this method.
        """
        self._debounce_timer.stop()
        self._dict_import_flow.cancel_active_batch()
        self._audio_pack_import_flow.cancel_active_batch()
        self._anki_probe.shutdown()

    # === Dictionary chain persistence ===

    def _sync_persisted_config(self, config: AnkiMinerConfig) -> None:
        """Keep durable content while preserving any committed version stamp."""
        self.config = replace(
            config,
            config_version=max(self.config.config_version, config.config_version),
        )

    def _commit_remove_config(self, config: AnkiMinerConfig) -> ConfigCommitResult:
        """Commit one remove and normalize its durable failure boundary."""
        try:
            self._commit_immediate_config(config, self._commit_config)
        except ConfigCommitError as error:
            if error.result.persisted:
                self._sync_persisted_config(config)
            return error.result
        except Exception as error:
            return ConfigCommitResult.pre_save_failure(error)
        return ConfigCommitResult.committed()

    def _commit_dictionary_removal(self, new_chain: tuple[object, ...]) -> ConfigCommitResult:
        chain = cast(tuple[ChainEntry, ...], new_chain)
        return self._commit_remove_config(replace(self.config, dictionary_chain=chain))

    def _commit_audio_removal(self, new_chain: tuple[object, ...]) -> ConfigCommitResult:
        chain = cast(tuple[AudioSourceEntry, ...], new_chain)
        return self._commit_remove_config(replace(self.config, expression_audio_chain=chain))

    def _commit_frequency_removal(self, new_chain: tuple[object, ...]) -> ConfigCommitResult:
        chain = cast(tuple[FreqEntry, ...], new_chain)
        return self._commit_remove_config(replace(self.config, frequency_chain=chain))

    def _commit_pitch_removal(self, new_chain: tuple[object, ...]) -> ConfigCommitResult:
        chain = cast(tuple[PitchSourceEntry, ...], new_chain)
        return self._commit_remove_config(replace(self.config, pitch_chain=chain))

    def _persist_chain_change(self, new_chain: tuple[ChainEntry, ...]) -> None:
        """Save a chain mutation to disk and notify listeners.

        Called after a successful import or panel reorder/toggle so the
        freshly imported dictionary is reachable on the very next lookup —
        without requiring a manual Save. Without this, the dict folder exists on
        disk but is absent from dictionary_chain in gui_config, i.e. invisible to
        DictionaryRegistry.build_provider_chain.

        A chain change alters which dictionaries' scoped CSS is embedded in the
        per-card ``<style>`` block, but that block is assembled per-episode at
        card-creation time (``EpisodeProcessor._phase5_create``), so nothing
        needs to sync to Anki here.
        """
        new_config = replace(self.config, dictionary_chain=new_chain)
        self._commit_immediate_config(new_config, self._commit_config)

    def _persist_audio_chain_change(self, new_chain: tuple[AudioSourceEntry, ...]) -> None:
        """Save an audio chain mutation to disk and notify listeners.

        Called after a successful audio pack import or panel reorder/toggle so
        the freshly-imported pack is reachable on the very next lookup without
        requiring the user to click Save in Settings.
        """
        new_config = replace(self.config, expression_audio_chain=new_chain)
        self._commit_immediate_config(new_config, self._commit_config)

    def _persist_reading_tts_change(self) -> None:
        """Save the sentence-TTS toggles immediately (no Save click needed)."""
        enabled, google_on, papago_on = self.audio_panel.get_reading_tts()
        new_config = replace(
            self.config,
            reading_tts_enabled=enabled,
            reading_tts_google_enabled=google_on,
            reading_tts_papago_enabled=papago_on,
        )
        self._commit_immediate_config(new_config, self.config_changed.emit)

    def _on_retry_missing_audio(self) -> None:
        """Clear JPod101 ``.miss`` markers so absent words are re-tried next run.

        Replaces the old folklore of deleting the ``audio_cache`` dir by hand.
        The unlink sweep runs off the GUI thread (run_off_thread convention);
        the removed count is confirmed in a dialog on completion.
        """
        cache_dir = ANKI_MINER_HOME / "audio_cache" / "jpod101"
        self.audio_panel.set_retry_missing_enabled(False)
        run_off_thread(
            self,
            lambda: purge_miss_markers(cache_dir),
            self._on_retry_missing_audio_done,
            self._on_retry_missing_audio_error,
        )

    def _on_retry_missing_audio_done(self, removed: object) -> None:
        """Re-enable the button and report how many markers were cleared."""
        self.audio_panel.set_retry_missing_enabled(True)
        count = removed if isinstance(removed, int) else 0
        QMessageBox.information(
            self,
            self.tr("Retry missing expression audio"),
            tr_format(
                self.tr("Cleared %1 missing-audio marker(s). Those words will be re-tried on the next mining run."),
                count,
            ),
        )

    def _on_retry_missing_audio_error(self, msg: str) -> None:
        """Re-enable the button and surface an unexpected sweep failure."""
        self.audio_panel.set_retry_missing_enabled(True)
        self.show_screen_issue(
            ScreenIssue(
                summary=self.tr("The missing-audio markers could not be cleared."),
                details=msg,
            )
        )

    def _persist_frequency_chain_change(self, new_chain: tuple[FreqEntry, ...]) -> None:
        """Save a frequency chain mutation to disk and notify listeners.

        Called after a successful frequency-source import or panel reorder/toggle
        so the freshly-imported source is reachable on the very next run without
        requiring the user to click Save in Settings.
        """
        new_config = replace(self.config, frequency_chain=new_chain)
        self._commit_immediate_config(new_config, self._commit_config)

    def _persist_pitch_chain_change(self, new_chain: tuple[PitchSourceEntry, ...]) -> None:
        """Save a pitch chain mutation to disk and notify listeners.

        Called after a successful pitch-source import or panel reorder/toggle
        so the freshly-imported source is reachable on the very next run without
        requiring the user to click Save in Settings.
        """
        new_config = replace(self.config, pitch_chain=new_chain)
        self._commit_immediate_config(new_config, self._commit_config)

    # === Known words handlers (Issues #38 / #42) ===

    def _on_rebuild_known_words(self) -> None:
        """Clear the local known-words cache after user confirmation.

        The cache is additive (see :class:`KnownWordDB`), so removing a deck's
        words after it was already synced requires a full rebuild. The next
        mining run re-syncs from Anki with the current exclusions applied.
        """
        confirm = QMessageBox.question(
            self,
            self.tr("Rebuild Known Words DB"),
            self.tr(
                "Clear the local known-words cache? It will re-sync from Anki on the "
                "next mining run, applying your current deck exclusions. Words you "
                "added yourself from the Word Curator are kept."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        try:
            db = KnownWordDB(self.config.known_words_db_path)
        except Exception as error:  # noqa: BLE001 - preserve the existing constructor boundary
            self._on_rebuild_known_words_error(str(error))
            return

        def work() -> int:
            db.initialize()
            # Preserve the user-curated ignore list (Issue #42); only the
            # Anki-synced rows are rebuilt from Anki on the next run.
            return db.clear(preserve_user=True)

        self.filtering_panel.rebuild_known_words_button.setEnabled(False)
        run_off_thread(
            self,
            work,
            lambda removed: self._on_rebuild_known_words_succeeded(
                lambda: QMessageBox.information(
                    self,
                    self.tr("Rebuild Known Words DB"),
                    tr_format(
                        self.tr("Cleared %1 cached word(s). The cache will rebuild on the next run."),
                        removed,
                    ),
                )
            ),
            self._on_rebuild_known_words_error,
            on_finished=self._on_rebuild_known_words_finished,
        )

    def _on_rebuild_known_words_succeeded(self, notify: Callable[[], object]) -> None:
        self.clear_screen_issue()
        notify()

    def _on_rebuild_known_words_error(self, message: str) -> None:
        self.show_screen_issue(
            ScreenIssue(
                summary=self.tr("The known-words cache could not be cleared."),
                details=message,
            )
        )

    def _on_rebuild_known_words_finished(self) -> None:
        self.filtering_panel.rebuild_known_words_button.setEnabled(True)

    def _on_manage_known_words(self) -> None:
        """Open the Manage Known Words dialog (Issue #42)."""
        from anki_miner.gui.widgets.dialogs.known_words_dialog import KnownWordsManagerDialog

        try:
            db = KnownWordDB(self.config.known_words_db_path)
            KnownWordsManagerDialog(db, self).exec()
        except Exception as e:  # noqa: BLE001 — surface any DB failure to the user
            self.show_screen_issue(
                ScreenIssue(
                    summary=self.tr("The known words list could not be opened."),
                    details=str(e),
                )
            )
