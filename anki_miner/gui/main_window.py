"""Main window for Anki Miner GUI."""

import logging
import sys
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QIcon, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from anki_miner import __version__
from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.constants import (
    WINDOW_DEFAULT_HEIGHT,
    WINDOW_DEFAULT_WIDTH,
    WINDOW_MIN_HEIGHT,
    WINDOW_MIN_WIDTH,
)
from anki_miner.gui.controllers import BackgroundTaskController
from anki_miner.gui.controllers.profile_controller import ProfileController
from anki_miner.gui.controllers.task_registry import TaskRegistry
from anki_miner.gui.launch import get_effective_log_path
from anki_miner.gui.presenters import GUIPresenter
from anki_miner.gui.resources import get_resource_dir
from anki_miner.gui.resources.styles.theme import Theme
from anki_miner.gui.utils import file_dialogs
from anki_miner.gui.utils.config_commit import ConfigCommitError, ConfigCommitResult
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.utils.run_off_thread import run_off_thread, still_running
from anki_miner.gui.widgets.dialogs.results_dialog import ResultsDialog
from anki_miner.gui.widgets.header_widget import HeaderWidget
from anki_miner.gui.widgets.status_bar_widget import StatusBarWidget
from anki_miner.models import ProcessingResult, ValidationResult
from anki_miner.services import ShortcutResult, ShortcutService, ValidationService
from anki_miner.services.anki_service import AnkiService
from anki_miner.utils.bundled_binary import frozen_state
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from anki_miner.gui.capabilities import CapabilityTarget
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizardOutcome

logger = logging.getLogger(__name__)


def open_log_folder(log_path: Path) -> None:
    """Open the parent directory of *log_path* in the system file manager."""
    from PyQt6.QtCore import QUrl
    from PyQt6.QtGui import QDesktopServices

    log_folder = Path(log_path).parent
    log_folder.mkdir(parents=True, exist_ok=True)
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_folder)))


class MainWindow(QMainWindow):
    """Main application window for Anki Miner.

    This window provides a tabbed interface for:
    - Video (container: Single episode / Batch folder / YouTube sub-tabs)
    - Deck Builder (corpus-driven deck assembly)
    - Audiobook (audio + subtitle pair queue)
    - Reading (container: Manga / Novels sub-tabs)
    - Analytics (mining statistics dashboard)
    - Tools (container: Generate / Retime subtitle sub-tabs)
    - Settings (configuration)

    Signals:
        config_refreshed: emitted with the post-save committed config after
            every ``update_config`` call. Tabs that cache services (and
            SettingsTab, so its panels don't go stale) reconnect this to their
            update_config to pick up the new state without waiting for the user
            to edit Settings.
    """

    config_refreshed = pyqtSignal(object)  # AnkiMinerConfig

    def __init__(self, config: AnkiMinerConfig | None = None):
        """Initialize the main window."""
        super().__init__()

        # In-memory re-entrancy guard for the deferred first-run setup offer.
        # The 0ms timer below could otherwise fire inside a nested modal event
        # loop (e.g. a freq-zip import) and re-enter on a half-built window.
        # NOT the persisted first_run_setup_done flag — purely runtime.
        self._first_run_setup_handled = False
        self._shortcut_work_in_flight = False
        self._boot_committed = False
        self._stale_dict_prompt_handled = False

        # Load configuration
        self.config = config if config is not None else GUIConfigManager.load_config()

        # Create presenter for validation signals
        self.presenter = GUIPresenter(self)

        # Background-task lifecycle controller (T-70): owns the validation /
        # update-check / JMdict-migration / prewarm worker handles and the
        # shutdown join policy. Results are forwarded back here — all UI
        # consumption (status bar, dialogs, banner, badge) stays in this class.
        self.background_tasks = BackgroundTaskController(self)
        self.background_tasks.validation_result.connect(self._on_validation_finished)
        self.background_tasks.validation_error.connect(self._on_validation_error)
        self.background_tasks.update_check_result.connect(self._on_update_check_result)
        self.background_tasks.ytdlp_update_result.connect(self._on_ytdlp_update_result)
        self.background_tasks.jmdict_migration_finished.connect(self._on_jmdict_migration_finished)

        # Settings-profile sequencing (boot reconcile / switch / create). Owned
        # here beside the other window-level controller, and constructed BEFORE
        # _setup_ui so the header can connect to it; it touches nothing until
        # commit_boot calls bootstrap().
        self.profile_controller = ProfileController(self)

        # The single record of what the app is currently doing. Owned here, not
        # by any tab, because a run has to stay visible after the user navigates
        # away from the screen that started it. It stores state only: worker
        # lifetime stays with BackgroundTaskController and the owning tab.
        # Constructed BEFORE _setup_ui so the status strip can bind to it.
        self.task_registry = TaskRegistry(self)

        # Config-bound services (validation + the AnkiService shared across undo
        # callbacks). Rebuilt on every config change via update_config — see
        # _build_config_bound_services — so an AnkiConnect URL/port edit reaches
        # the next Undo delete instead of the stale startup endpoint.
        self._build_config_bound_services()
        self._validation_silent = True

        # Connect presenter signals
        self._connect_presenter_signals()

        # Set up UI
        self._setup_ui()

        # Singleton update banner — None until the first check yields a result.
        # Reused across update checks via UpdateBanner.update_info() to avoid
        # racing in-flight Qt callbacks against a destroyed C++ object.
        from anki_miner.gui.widgets.update_banner import UpdateBanner

        self._update_banner: UpdateBanner | None = None

    def commit_boot(self, *, suppress_optional: bool = False) -> None:
        """Commit startup state, then start boot work unless suppressed."""
        if self._boot_committed:
            return

        # FIRST, and deliberately OUTSIDE the suppress_optional gate.
        # First: the last_known_version save below is a save, and a save that
        # runs before the reconcile has seeded GUIConfigManager.ACTIVE_PROFILE_ID
        # writes gui_config.json with no profile marker.
        # Outside the gate: bootstrap is pure local file I/O — no network, no
        # dialogs — and the suppressed path is the installer smoke, which
        # asserts on the gui_config.json that same save produces. The wrapper is
        # here only for its log-and-swallow.
        self._run_optional_boot_step("settings profiles", self.profile_controller.bootstrap)

        if not suppress_optional:
            self._run_optional_boot_step(
                "legacy frequency-source repair",
                self._maybe_repair_legacy_frequency_source_name,
            )
            # One-time legacy pitch_accent.csv → pitch/legacy-pitch migration.
            # Synchronous (CSV→sqlite is fast and one-time) and must run before
            # any pitch-consuming service is built.
            self._run_optional_boot_step(
                "legacy pitch migration",
                self._maybe_migrate_legacy_pitch,
            )

        previous = self.config.last_known_version
        if previous != __version__:
            self.update_config(replace(self.config, last_known_version=__version__))

        if not suppress_optional and previous and previous != __version__:
            QMessageBox.information(
                self,
                self.tr("Anki Miner updated"),
                tr_format(
                    self.tr(
                        "Updated to v%1.<br><br>"
                        "See what's new: "
                        '<a href="https://github.com/0xzerolight/anki_miner/releases/latest">'
                        "release notes</a>"
                    ),
                    __version__,
                ),
            )

        self._boot_committed = True
        if suppress_optional:
            return

        self._validation_silent = True
        self._run_optional_boot_step("startup validation", self._run_validation)
        if self.config.check_for_updates:
            self._run_optional_boot_step("update check", self._check_for_updates)
        self._run_optional_boot_step("JMdict migration", self._maybe_migrate_jmdict)
        self._run_optional_boot_step("yt-dlp update", self._maybe_start_ytdlp_update)

        if not self.config.first_run_shortcut_done:
            QTimer.singleShot(0, self._maybe_create_shortcut_on_first_run)
        if not self.config.first_run_setup_done:
            QTimer.singleShot(0, self._maybe_offer_first_run_setup)
        QTimer.singleShot(0, self._maybe_prompt_stale_dictionaries)

    @staticmethod
    def _run_optional_boot_step(name: str, step: Callable[[], None]) -> None:
        try:
            step()
        except Exception:
            logger.exception("Optional boot step failed: %s", name)

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        self.setWindowTitle("Anki Miner")
        self.setMinimumSize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)
        self.resize(WINDOW_DEFAULT_WIDTH, WINDOW_DEFAULT_HEIGHT)

        # Create central widget with layout
        central_widget = QWidget()
        self.central_layout = QVBoxLayout()
        self.central_layout.setContentsMargins(0, 0, 0, 0)
        self.central_layout.setSpacing(0)

        # Add header
        self.header = HeaderWidget()
        self.header.theme_changed.connect(self._on_theme_changed)
        self.header.open_theme_settings.connect(self._open_theme_settings)
        # The combo only ever proposes a switch: the controller decides, shows
        # any refusal itself and snaps the combo back on every terminal path.
        self.header.profile_changed.connect(self.profile_controller.switch_to)
        self.header.open_profile_manager.connect(self._open_profile_manager)
        self.central_layout.addWidget(self.header)

        # Create tab widget
        self.tabs = QTabWidget()
        self.central_layout.addWidget(self.tabs)

        central_widget.setLayout(self.central_layout)
        self.setCentralWidget(central_widget)

        # Enhanced status bar
        self.status_bar = StatusBarWidget()
        self.status_bar.system_status_clicked.connect(self._on_system_status_clicked)
        self.status_bar.task_activated.connect(self._on_task_activated)
        self.status_bar.bind_task_registry(self.task_registry)
        self.setStatusBar(self.status_bar)

        # Set up menu bar
        self._setup_menu_bar()

        # Set up keyboard shortcuts
        self._setup_shortcuts()

        # Set up accessibility features
        self._setup_accessibility()

    def _setup_accessibility(self) -> None:
        """Set up accessibility features for screen readers and keyboard navigation."""
        # Set window accessible name and description
        self.setAccessibleName(self.tr("Anki Miner Main Window"))
        self.setAccessibleDescription(
            self.tr("Japanese vocabulary mining tool for creating Anki flashcards from video subtitles")
        )

        # Set accessible names for main components
        self.tabs.setAccessibleName(self.tr("Main Tabs"))
        self.tabs.setAccessibleDescription(
            self.tr("Navigate between Video, Deck Builder, Audio, Reading, Analytics, Tools, and Settings")
        )

        self.header.setAccessibleName(self.tr("Application Header"))
        self.header.setAccessibleDescription(self.tr("Application title and theme selector"))

        self.status_bar.setAccessibleName(self.tr("Status Bar"))
        self.status_bar.setAccessibleDescription(self.tr("Shows current operation, statistics, and system status"))

        # Set tab order: header -> tabs -> status bar
        self.setTabOrder(self.header, self.tabs)
        self.setTabOrder(self.tabs, self.status_bar)

    def _setup_menu_bar(self) -> None:
        """Set up the application menu bar."""
        menu_bar = self.menuBar()
        assert menu_bar is not None

        # Tools menu
        tools_menu = menu_bar.addMenu(self.tr("&Tools"))
        assert tools_menu is not None
        shortcut_action = tools_menu.addAction(self.tr("Create Desktop Shortcut..."))
        assert shortcut_action is not None
        shortcut_action.triggered.connect(self._create_desktop_shortcut)

        resources_action = tools_menu.addAction(self.tr("Download Recommended Resources..."))
        assert resources_action is not None
        resources_action.triggered.connect(self._download_recommended_resources)

        find_feature_action = tools_menu.addAction(self.tr("Find a Feature..."))
        assert find_feature_action is not None
        find_feature_action.triggered.connect(self._run_capability_browser_tool)

        setup_wizard_action = tools_menu.addAction(self.tr("Setup Wizard..."))
        assert setup_wizard_action is not None
        setup_wizard_action.triggered.connect(self._run_setup_wizard_tool)

        restyle_action = tools_menu.addAction(self.tr("Restyle Mined Cards..."))
        assert restyle_action is not None
        restyle_action.triggered.connect(self._restyle_mined_cards)

        # Help menu
        help_menu = menu_bar.addMenu(self.tr("&Help"))
        assert help_menu is not None

        about_action = help_menu.addAction(self.tr("About Anki Miner"))
        assert about_action is not None
        about_action.setShortcut(QKeySequence("F1"))
        about_action.triggered.connect(self._show_about)

        help_menu.addSeparator()

        check_updates_action = help_menu.addAction(self.tr("Check for Updates"))
        assert check_updates_action is not None
        check_updates_action.triggered.connect(self._check_for_updates)

        help_menu.addSeparator()

        open_log_action = help_menu.addAction(self.tr("Open Log Folder"))
        assert open_log_action is not None
        open_log_action.setToolTip(self.tr("Open the log folder in your file manager"))
        open_log_action.triggered.connect(self._open_log_folder)

        # Top-right corner of the menu bar holds a small button bar. A QMenuBar
        # allows only one corner widget per corner, so both buttons live inside
        # a container QWidget laid out horizontally.
        corner_widget = QWidget(menu_bar)
        # Named so common.qss can paint its background with the theme's window
        # color; without it the strip behind the buttons stays white in dark
        # mode on Windows (native menu-bar default).
        corner_widget.setObjectName("menu_corner_widget")
        corner_layout = QHBoxLayout(corner_widget)
        corner_layout.setContentsMargins(0, 0, 0, 0)
        corner_layout.setSpacing(0)

        # "Report a Bug / Suggest a Feature" button (moved out of the Help menu).
        report_button = QToolButton(corner_widget)
        report_button.setObjectName("report_issue_button")
        report_button.setText(self.tr("Report a Bug / Suggest a Feature"))
        report_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        report_button.setAutoRaise(True)
        report_button.setToolTip(self.tr("Report a bug or suggest a feature on GitHub"))
        report_button.clicked.connect(self._report_issue)
        corner_layout.addWidget(report_button)

        # "Star on GitHub" button.
        star_button = QToolButton(corner_widget)
        star_button.setObjectName("github_star_button")
        star_button.setText(self.tr("⭐ Star - help the project"))
        star_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        star_button.setAutoRaise(True)
        star_button.setToolTip(self.tr("Star the project on GitHub"))
        star_button.clicked.connect(self._open_github_repo)
        corner_layout.addWidget(star_button)

        # "Join Discord" button — brand mark beside the label.
        discord_button = QToolButton(corner_widget)
        discord_button.setObjectName("discord_button")
        discord_button.setText(self.tr("Join Discord"))
        discord_button.setAutoRaise(True)
        discord_button.setToolTip(self.tr("Join the community on Discord"))
        # Guard on the loaded icon (covers a missing OR unparseable SVG): a
        # TextBesideIcon button with a null icon would leave a blank gap, so fall
        # back to text-only if the brand mark fails to load.
        discord_icon = QIcon(str(get_resource_dir() / "icons" / "discord.svg"))
        if not discord_icon.isNull():
            discord_button.setIcon(discord_icon)
            # Pin the glyph size so it stays independent of Qt/QSS icon defaults.
            discord_button.setIconSize(QSize(16, 16))
            discord_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        else:
            discord_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        discord_button.clicked.connect(self._open_discord)
        corner_layout.addWidget(discord_button)

        menu_bar.setCornerWidget(corner_widget, Qt.Corner.TopRightCorner)

    def _setup_shortcuts(self) -> None:
        """Set up global keyboard shortcuts.

        Per-tab Ctrl+N shortcuts are NOT created here — they depend on the live
        tab count and are created by :meth:`setup_tab_shortcuts`, which app.py
        calls once all tabs have been registered via :func:`register_mining_tab`.
        """
        # Theme toggle (Ctrl+T)
        theme_shortcut = QShortcut(QKeySequence("Ctrl+T"), self)
        theme_shortcut.activated.connect(self._cycle_theme)

        # Settings shortcut (Ctrl+,)
        settings_shortcut = QShortcut(QKeySequence("Ctrl+,"), self)
        settings_shortcut.activated.connect(self._open_settings)

        # System validation (Ctrl+Shift+V)
        validation_shortcut = QShortcut(QKeySequence("Ctrl+Shift+V"), self)
        validation_shortcut.activated.connect(self._run_validation)

    def setup_tab_shortcuts(self) -> None:
        """Create one Ctrl+N shortcut per registered tab, driven by the live tab count.

        Called by app.py after all tabs have been registered so the count is
        final.  Creating these in :meth:`_setup_shortcuts` (which runs in
        ``__init__``, before app.py adds any tabs) would under-count and leave
        the later tabs unreachable.
        """
        for i in range(1, self.tabs.count() + 1):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{i}"), self)
            shortcut.activated.connect(lambda idx=i - 1: self._switch_to_tab(idx))

    def _switch_to_tab(self, index: int) -> None:
        """Switch to tab at given index.

        Args:
            index: Tab index (0-based)
        """
        if 0 <= index < self.tabs.count():
            self.tabs.setCurrentIndex(index)

    def _cycle_theme(self) -> None:
        """Cycle through favorited themes (Ctrl+T)."""
        new_mode = Theme.cycle_theme()

        # Update combo box to reflect the new theme
        self.header.update_theme_selector()

        # Apply theme + persist
        app = QApplication.instance()
        if isinstance(app, QApplication):
            Theme.apply_to_app(app, new_mode)
        if new_mode != self.config.theme:
            self.update_config(replace(self.config, theme=new_mode))

    def _settings_tab_index(self) -> int:
        """Locate the Settings tab by capability (self-healing against tab reorder)."""
        for i in range(self.tabs.count()):
            if hasattr(self.tabs.widget(i), "open_ui_subtab"):
                return i
        return -1

    @contextmanager
    def _dictionary_mutation_guard(self, kind: str) -> Iterator[bool]:
        """Commit pending Settings, then own dictionary mutation controls."""
        if not self.prepare_dictionary_mutation():
            yield False
            return
        settings_idx = self._settings_tab_index()
        if settings_idx < 0:
            yield True
            return
        settings_tab = self.tabs.widget(settings_idx)
        preflight = getattr(settings_tab, "commit_pending_settings_for_mutation", None)
        panel = getattr(settings_tab, "dictionary_panel", None)
        if not callable(preflight) or panel is None:
            yield True
            return
        if not preflight():
            yield False
            return
        token = panel.hold_mutation(kind)
        try:
            yield True
        finally:
            panel.release(token)

    def prepare_dictionary_mutation(self) -> bool:
        """Stop startup JMdict migration or show the shared refusal dialog."""
        if self.background_tasks.prepare_dictionary_mutation():
            return True
        QMessageBox.warning(
            self,
            self.tr("Dictionary Change Blocked"),
            self.tr("The startup JMdict migration is still stopping. Wait for it to finish and try again."),
        )
        return False

    # Stable capability key -> the widget class name registered as that main tab.
    # Matched by class name (not index/label) so it survives tab reorder and i18n.
    _MAIN_TAB_CLASSES = {
        "video": "VideoTab",
        "deckbuilder": "DeckBuilderTab",
        "audiobook": "AudiobookTab",
        "reading": "ReadingTab",
        "analytics": "AnalyticsTab",
        "subtitles": "SubtitlesTab",
        "settings": "SettingsTab",
    }

    def _main_tab_index(self, key: str) -> int:
        """Locate a top-level tab by stable capability key; -1 if absent."""
        if key == "settings":
            return self._settings_tab_index()
        class_name = self._MAIN_TAB_CLASSES.get(key)
        if class_name is None:
            return -1
        for i in range(self.tabs.count()):
            if type(self.tabs.widget(i)).__name__ == class_name:
                return i
        return -1

    def reveal_capability(self, target: "CapabilityTarget") -> None:
        """Bring the tab that hosts ``target`` to the front (and its sub-tab).

        Called by the Find a Feature browser. No-ops silently if the tab can't be
        found (e.g. an optional tab was not registered) so a stale catalogue entry
        never crashes the UI.
        """
        idx = self._main_tab_index(target.main_tab)
        if idx < 0:
            return
        self.tabs.setCurrentIndex(idx)
        if target.subtab:
            container = self.tabs.widget(idx)
            open_subtab = getattr(container, "open_subtab", None)
            if callable(open_subtab):
                open_subtab(target.subtab)

    def _on_task_activated(self, task_id: str) -> None:
        """Take the user to the screen that owns ``task_id``.

        Routed through the task's own ``CapabilityTarget`` and the same stable
        key lookup the feature browser uses, so a task never has to know a tab
        index. An unknown id is a silent no-op: the run may have been dropped
        between the menu opening and the choice.
        """
        snapshot = self.task_registry.snapshot(task_id)
        if snapshot is not None:
            self.reveal_capability(snapshot.owner)

    def _open_settings(self) -> None:
        """Open the Settings tab."""
        idx = self._settings_tab_index()
        if idx >= 0:
            self.tabs.setCurrentIndex(idx)

    def _open_theme_settings(self) -> None:
        """Switch to Settings → UI (triggered by 'All themes…' sentinel).

        The theme list now lives on the UI sub-tab (alongside language/zoom/
        text size), so this lands there.
        """
        idx = self._settings_tab_index()
        if idx < 0:
            return
        self.tabs.setCurrentIndex(idx)
        # Call through to the Settings tab's convenience method to land on the right sub-tab.
        settings_widget = self.tabs.widget(idx)
        open_subtab = getattr(settings_widget, "open_ui_subtab", None)
        if callable(open_subtab):
            open_subtab()

    def _open_profile_manager(self) -> None:
        """Open the settings-profile manager (header sentinel / Settings → UI).

        ``exec``, never ``show``: the dialog sets no modality of its own, and a
        modeless one would be repainted mid-CRUD by the settings reload a switch
        fans out — the hazard the modal shape exists to avoid.

        The refresh hook is the controller's own ``sync_header``: the dialog's
        rename/delete paths go straight to ``ProfileStore`` and never pass
        through a switch, so they need the same re-point every terminal path of
        a switch already runs.
        """
        from anki_miner.gui.widgets.dialogs.profile_manager_dialog import ProfileManagerDialog

        ProfileManagerDialog(self.profile_controller, self.profile_controller.sync_header, self).exec()

    def _report_issue(self) -> None:
        """Open the GitHub issues page in the default browser."""
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl("https://github.com/0xzerolight/anki_miner/issues"))

    def _open_github_repo(self) -> None:
        """Open the GitHub repository in the default browser."""
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl("https://github.com/0xzerolight/anki_miner"))

    def _open_discord(self) -> None:
        """Open the Discord community invite in the default browser."""
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl("https://discord.com/invite/aDtQyZzUVP"))

    def _open_log_folder(self) -> None:
        """Open the log folder in the system file manager (Help → Open Log Folder)."""
        open_log_folder(get_effective_log_path(self.config.log_path))

    def _create_desktop_shortcut(self) -> None:
        """Create a desktop shortcut via ShortcutService and report the result."""
        self._run_shortcut_work(show_result=True, skip_if_exists=False, include_start_menu=False)

    def _maybe_create_shortcut_on_first_run(self) -> None:
        """Auto-create a desktop shortcut on first launch; persist the flag."""
        if sys.platform == "win32" and frozen_state()[0]:
            if not self.config.first_run_shortcut_done:
                try:
                    self.update_config(replace(self.config, first_run_shortcut_done=True))
                except Exception:
                    logger.exception("Could not persist desktop shortcut attempt state")
            return
        self._run_shortcut_work(show_result=False, skip_if_exists=True, include_start_menu=True)

    def _run_shortcut_work(
        self,
        *,
        show_result: bool,
        skip_if_exists: bool,
        include_start_menu: bool,
    ) -> None:
        if self._shortcut_work_in_flight:
            return
        self._shortcut_work_in_flight = True

        def work() -> ShortcutResult | None:
            if sys.platform == "win32":
                return ShortcutService.create_shortcut(
                    skip_if_exists=skip_if_exists,
                    include_start_menu=include_start_menu,
                )
            if skip_if_exists and ShortcutService.shortcut_exists():
                return None
            return ShortcutService.create_shortcut()

        def finish_attempt() -> None:
            self._shortcut_work_in_flight = False
            if not self.config.first_run_shortcut_done:
                try:
                    self.update_config(replace(self.config, first_run_shortcut_done=True))
                except Exception:
                    logger.exception("Could not persist desktop shortcut attempt state")

        def on_done(value: object) -> None:
            finish_attempt()
            if not show_result or value is None:
                return
            if not isinstance(value, ShortcutResult):
                QMessageBox.warning(self, self.tr("Desktop Shortcut"), self.tr("Failed to create desktop shortcut."))
                return
            body = "\n".join(value.messages) if value.messages else ""
            if value.success:
                QMessageBox.information(self, self.tr("Desktop Shortcut"), body or self.tr("Shortcut created."))
            else:
                QMessageBox.warning(
                    self,
                    self.tr("Desktop Shortcut"),
                    value.error or self.tr("Failed to create desktop shortcut."),
                )

        def on_error(message: str) -> None:
            finish_attempt()
            logger.warning("Desktop shortcut attempt failed: %s", message)
            if show_result:
                QMessageBox.warning(self, self.tr("Desktop Shortcut"), message)

        try:
            run_off_thread(self, work, on_done, on_error, on_finished=finish_attempt)
        except Exception as exc:
            on_error(str(exc))

    def _download_recommended_resources(self) -> None:
        """Tools-menu handler: run the resource download dialog, apply result."""
        from anki_miner.gui.widgets.dialogs.resource_download_dialog import run_resource_download

        with self._dictionary_mutation_guard("resource-download") as ready:
            if not ready:
                return
            # The recommended dict downloads into the same slot the legacy
            # JMdict XML migration writes; stop an in-flight migration first.
            self.background_tasks.cancel_jmdict_migration()
            outcome = run_resource_download(self, self.config, release_resources=self.release_dictionary_resources)
            if outcome is not None and outcome.summary.succeeded:
                # update_config (not from_settings) propagates via
                # config_refreshed to all tabs incl. Settings, and persists.
                self.update_config(outcome.config)

    def _run_capability_browser_tool(self) -> None:
        """Tools-menu handler: open the Find a Feature browser.

        The dialog drives navigation through :meth:`reveal_capability`; it does
        not modify config, so there is nothing to apply on return.
        """
        from anki_miner.gui.widgets.dialogs.capability_browser import run_capability_browser

        run_capability_browser(self, self)

    def _run_setup_wizard_tool(self) -> None:
        """Tools-menu handler: re-run the guided setup wizard (re-runnable).

        Unlike the first-run offer, this NEVER touches ``first_run_setup_done`` —
        it just applies the wizard's returned config via ``update_config`` so
        deck/note-type/fields/resources propagate and services rebuild.
        """
        from anki_miner.gui.widgets.dialogs.setup_wizard import run_setup_wizard

        with self._dictionary_mutation_guard("setup-wizard") as ready:
            if not ready:
                return
            # Wizard's Resources page can download into the JMdict migration slot.
            self.background_tasks.cancel_jmdict_migration()
            outcome = run_setup_wizard(self, self.config)
            self._commit_setup_wizard_outcome(outcome, first_run_offer=False)

    def _commit_setup_wizard_outcome(
        self,
        outcome: "SetupWizardOutcome",
        *,
        first_run_offer: bool,
    ) -> None:
        """Merge live one-way flags, then persist one wizard outcome."""
        live_config = self.config
        setup_done = (
            live_config.first_run_setup_done or outcome.consumes_first_run_offer
            if first_run_offer
            else live_config.first_run_setup_done
        )
        merged = replace(
            outcome.config,
            first_run_shortcut_done=(live_config.first_run_shortcut_done or outcome.config.first_run_shortcut_done),
            first_run_setup_done=setup_done,
        )
        self.update_config(merged)

    def _restyle_mined_cards(self) -> None:
        """Tools-menu handler: re-apply the built-in glossary styling to already-mined cards.

        Idempotent and content-preserving: prepends the self-contained ``<style>``
        block to cards that lack the base sheet, and refreshes the embedded base
        head in place on cards that already carry one — so a styling change reaches
        existing cards (see :func:`card_restyler.restyle_mined_cards`). Runs
        off-thread via ``BackgroundTaskController`` (joined at close).
        """
        from anki_miner.services.card_restyler import RestyleResult

        reply = QMessageBox.question(
            self,
            self.tr("Restyle Mined Cards"),
            self.tr(
                "Re-apply the latest built-in styling to your mined cards so they match "
                "new ones. Safe to re-run; it never removes card content.\n\nClose Anki's "
                "card browser and any open note editor first — editing an open note can "
                "lose unsaved edits.\n\nContinue?"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            service = AnkiService(self.config)
        except ValueError as exc:
            # Corrupted anki_fields — surface, don't crash the slot (mirror
            # AnkiProbeController's guarded construction).
            logger.warning("Cannot build AnkiService for restyle: %s", exc)
            QMessageBox.warning(
                self,
                self.tr("Restyle Mined Cards"),
                tr_format(self.tr("Cannot start restyle — Anki fields are misconfigured: %1"), str(exc)),
            )
            return
        self.status_bar.set_operation(self.tr("Restyling mined cards…"), "info")

        def on_progress(scanned: int, total: int) -> None:
            self.status_bar.set_operation(tr_format(self.tr("Restyling mined cards… %1/%2"), scanned, total), "info")

        def on_result(result: RestyleResult) -> None:
            self.status_bar.set_operation(self.tr("Restyle complete"), "success")
            QMessageBox.information(
                self,
                self.tr("Restyle Mined Cards"),
                tr_format(
                    self.tr("Restyled %1 card(s). (%2 scanned; %3 already up to date.)"),
                    result.restyled,
                    result.scanned,
                    result.skipped_styled,
                ),
            )

        def on_error(message: str) -> None:
            self.status_bar.set_operation(self.tr("Restyle failed"), "error")
            QMessageBox.warning(self, self.tr("Restyle Mined Cards"), message)

        self.background_tasks.start_restyle_cards(service, self.config, on_progress, on_result, on_error)

    def _maybe_offer_first_run_setup(self) -> None:
        """Offer guided setup and consume the offer only on finish or Skip.

        Broadened (Task 3): the wizard is offered whenever the run hasn't been
        completed (``not first_run_setup_done``) — no longer gated on freq/pitch
        file presence, since the wizard's Resources step covers those. The
        wizard's returned partial config is always persisted. Dismissal leaves
        the offer unconsumed; failures are logged and re-offered next launch.
        """
        from anki_miner.gui.widgets.dialogs.setup_wizard import run_setup_wizard

        # Re-entrancy / idempotency guard: never run twice, and never re-enter if
        # the 0ms timer fires inside a nested modal loop. Set before any work so a
        # re-entrant fire during the wizard's exec() bails out immediately.
        if self._first_run_setup_handled:
            return
        self._first_run_setup_handled = True

        with self._dictionary_mutation_guard("first-run-setup-wizard") as ready:
            if not ready:
                self._first_run_setup_handled = False
                return
            # Boot started the legacy JMdict XML migration just before
            # scheduling this offer; stop it before the wizard captures config.
            self.background_tasks.cancel_jmdict_migration()

            try:
                outcome = run_setup_wizard(self, self.config)
            except Exception:
                logger.exception("Setup wizard failed")
                return
            self._commit_setup_wizard_outcome(outcome, first_run_offer=True)

    def _maybe_prompt_stale_dictionaries(self) -> None:
        """Dispatch the schema-staleness scan off-thread; prompt in the callback (4.0).

        The probe builds a fresh registry and reads every enabled dictionary's
        index sidecar (per-dict SQLite), so it runs on a worker thread via
        ``run_off_thread`` rather than blocking the GUI during startup. The
        Reimport prompt is shown from ``_on_stale_dicts_scanned`` on the GUI
        thread. The ``QTimer.singleShot`` startup deferral is unchanged.
        """
        if self._stale_dict_prompt_handled:
            return
        from anki_miner.services.dictionary.registry import stale_enabled_dicts

        config = self.config
        run_off_thread(self, lambda: stale_enabled_dicts(config), self._on_stale_dicts_scanned)

    def _on_stale_dicts_scanned(self, result: object) -> None:
        """GUI-thread continuation: prompt to Reimport All for any stale dicts found.

        Detection reused the registry seam (``stale_enabled_dicts`` → per-slot
        ``DictMeta.schema_ok``), not a new scanner. When any *enabled* indexed
        chain entry is schema-stale, mining would silently drop every word for
        lack of a definition, so we surface a blocking prompt offering one-click
        repair scoped to those stale slots (covering both yomitan ``source.zip``
        slots and the legacy JMdict slot; slots without a saved source are named
        in its summary and fall to the per-row affordance). "Later" leaves mining
        gated by the per-run pre-checks; the prompt re-offers next launch.
        """
        if self._stale_dict_prompt_handled:
            return
        stale = list(result) if isinstance(result, list) else []
        if not stale:
            return
        # Set before exec() so a re-entrant 0ms fire inside the modal loop bails.
        self._stale_dict_prompt_handled = True

        names = "\n".join(f"  • {m.source_name}" for m in stale)
        body = (
            self.tr("These dictionaries need re-importing after an app upgrade (their index format changed):")
            + f"\n\n{names}\n\n"
            + self.tr("Mining is blocked for them until you do. Re-import them now?")
        )
        reply = QMessageBox.question(
            self,
            self.tr("Dictionaries need re-importing"),
            body,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        idx = self._settings_tab_index()
        if idx < 0:
            return
        self.tabs.setCurrentIndex(idx)
        settings_widget = self.tabs.widget(idx)
        trigger = getattr(settings_widget, "trigger_reimport_all", None)
        if callable(trigger):
            trigger(frozenset(m.dict_id for m in stale))

    def _show_about(self) -> None:
        """Show the About dialog."""
        from anki_miner.gui.widgets.dialogs.about_dialog import AboutDialog

        AboutDialog(__version__, self).exec()

    def _connect_presenter_signals(self) -> None:
        """Connect presenter signals to UI update slots."""
        self.presenter.info_signal.connect(self._on_info_message)
        self.presenter.success_signal.connect(self._on_success_message)
        self.presenter.warning_signal.connect(self._on_warning_message)
        self.presenter.error_signal.connect(self._on_error_message)
        self.presenter.validation_result_signal.connect(self._on_validation_result)
        self.presenter.processing_result_signal.connect(self._on_processing_result)

    def _on_info_message(self, message: str) -> None:
        """Handle info message from presenter.

        Args:
            message: Info message to display
        """
        self.status_bar.set_operation(message, "info")

    def _on_success_message(self, message: str) -> None:
        """Handle success message from presenter.

        Args:
            message: Success message to display
        """
        self.status_bar.set_operation(message, "success")

    def _on_warning_message(self, message: str) -> None:
        """Handle warning message from presenter.

        Args:
            message: Warning message to display
        """
        self.status_bar.set_operation(message, "warning")

    def _on_error_message(self, message: str) -> None:
        """Handle error message from presenter.

        Args:
            message: Error message to display
        """
        self.status_bar.set_operation(message, "error")

    def _on_validation_result(self, result: ValidationResult) -> None:
        """Handle validation result from presenter.

        Args:
            result: Validation result to display
        """
        silent = self._validation_silent
        self._validation_silent = False

        if silent:
            if not result.issues:
                logger.info("Startup validation completed: issues=0")
            else:
                component_counts = Counter(issue.component for issue in result.issues)
                logger.warning(
                    "Startup validation completed: issues=%d errors=%d warnings=%d components=%s",
                    len(result.issues),
                    len(result.get_errors()),
                    len(result.get_warnings()),
                    ",".join(f"{name}={count}" for name, count in sorted(component_counts.items())),
                )

        # Update system status indicators
        ankiconnect_ok = all(issue.component != "AnkiConnect" for issue in result.issues)
        ffmpeg_ok = all(issue.component != "ffmpeg" for issue in result.issues)
        self.status_bar.set_system_status(ankiconnect_ok, ffmpeg_ok)

        # Route the yt-dlp verdict into Settings → YouTube. Validation is the single
        # producer here on purpose: it already ran `yt-dlp --version` off the GUI
        # thread, so the panel never has to spawn a subprocess on a load path (which
        # the repo's GUI-thread tripwire forbids).
        self._set_ytdlp_status_from_validation(result)

        # Drive the Settings → Anki connection badge so Test Connection (the
        # only button still routed through validation — the deck/note-type
        # refresh buttons now reload the dropdowns instead) produces visible
        # feedback (T-53). The badge otherwise sticks at
        # "Checking connection..." forever — set_connection_status had no
        # callers. Use the authoritative result.ankiconnect_ok flag.
        self._set_anki_connection_badge("connected" if result.ankiconnect_ok else "disconnected")

        if result.all_passed:
            self.status_bar.set_operation(self.tr("System validation passed"), "success")
        elif not silent:
            # Show validation issues (skip popup during startup auto-check)
            issues_text = "\n".join([f"- {issue.component}: {issue.message}" for issue in result.issues])
            QMessageBox.warning(
                self,
                self.tr("Validation Issues"),
                tr_format(self.tr("System validation found issues:\n\n%1"), issues_text),
            )

    def _set_anki_connection_badge(self, status: str) -> None:
        """Push an AnkiConnect connection status onto the Settings → Anki badge.

        Locates the Settings tab by capability (same self-healing lookup as
        :meth:`_settings_tab_index`, so it survives tab reorders) and forwards
        to ``AnkiSettingsPanel.set_connection_status``. A no-op when the Settings
        tab or its ``anki_panel`` is absent — e.g. mid-teardown or in tests that
        build a bare window — so validation never crashes for want of a badge.

        Args:
            status: one of "connected", "disconnected", "checking", "unknown".
        """
        idx = self._settings_tab_index()
        if idx < 0:
            return
        panel = getattr(self.tabs.widget(idx), "anki_panel", None)
        if panel is not None:
            panel.set_connection_status(status)

    def _set_ytdlp_status_from_validation(self, result: object) -> None:
        """Push the yt-dlp validation verdict onto Settings → YouTube.

        Validation is the single producer of this text: it resolves and probes the
        binary off the GUI thread and reports both the version and which tier it came
        from. Having the panel compute it at load time instead would put a
        ``yt-dlp --version`` subprocess on the GUI thread.

        A no-op when the Settings tab is absent (mid-teardown, or a bare window in
        tests), so validation never crashes for want of a status line.
        """
        idx = self._settings_tab_index()
        if idx < 0:
            return
        tab = self.tabs.widget(idx)
        setter = getattr(tab, "set_ytdlp_status", None)
        if setter is None:
            return

        issues = getattr(result, "issues", None) or []
        problems = [issue.message for issue in issues if getattr(issue, "component", "") == "yt-dlp"]
        if problems:
            setter(problems[0])
            return
        versions = getattr(result, "tool_versions", None) or {}
        setter(versions.get("yt-dlp", ""))

    def reload_settings_panels(self) -> None:
        """Repaint the Settings tab's panels from the live config.

        ``SettingsTab.update_config`` deliberately SKIPS its reload when an
        incoming diff falls entirely inside its externally-managed allowlist, so
        an unrelated commit cannot destroy unsaved panel edits (OVH-007). A
        settings-profile switch between two profiles that differ only in
        appearance produces exactly that diff, so ``ProfileController`` calls
        this after a durable switch to force the redraw the gate suppressed.

        Same self-healing lookup and same absent-tab tolerance as
        :meth:`_set_anki_connection_badge`.
        """
        idx = self._settings_tab_index()
        if idx < 0:
            return
        reload_panels = getattr(self.tabs.widget(idx), "reload_from_config", None)
        if callable(reload_panels):
            reload_panels(self.config)

    def _on_processing_result(self, result: ProcessingResult) -> None:
        """Handle processing result from presenter.

        Args:
            result: Processing result to display
        """
        # Update session statistics
        self.status_bar.increment_cards_created(result.cards_created)

        # Create undo callback. This is the BLOCKING work handed to
        # ResultsDialog, which runs it off the GUI thread (a slow AnkiConnect
        # delete must not freeze the modal dialog) — so it must not touch Qt
        # widgets. The session-counter decrement runs on the GUI thread via the
        # on_undo_committed continuation below.
        def undo_callback(note_ids: list[int]) -> int:
            if self._anki_service is None:
                raise RuntimeError("Anki service is unavailable; check the note-type field mapping.")
            deleted = self._anki_service.delete_notes(note_ids)
            # Revert the session's source='mined' known-words rows so the user
            # can re-mine the same words on the next run (OVH-030). Only the
            # 'mined' rows written by this session are removed — source='user'
            # and source='anki' rows are untouched (Issue #42). Gate on the DB
            # being available, NOT on use_known_words_db: the mining write
            # (episode_processor) records 'mined' rows whenever the DB file
            # exists, regardless of the toggle, so undo must revert under the
            # same condition or it leaves orphaned 'mined' rows that suppress
            # re-mining if the toggle is later enabled (F2). Guard with
            # try/except so a DB failure never crashes the GUI.
            if result.mined_forms:
                try:
                    from anki_miner.services.known_word_db import KnownWordDB

                    kw_db = KnownWordDB(self.config.known_words_db_path)
                    if kw_db.is_available():
                        kw_db.remove_words(set(result.mined_forms), source="mined")
                except Exception:
                    logger.warning("Undo: could not revert mined words in known_words.db", exc_info=True)
            return deleted

        # Show results dialog with undo support. The dialog runs undo_callback
        # off-thread; on_undo_committed decrements the session counter on the
        # GUI thread once the delete succeeds.
        dialog = ResultsDialog(
            result,
            self,
            undo_callback=undo_callback,
            on_undo_committed=lambda deleted: self.status_bar.increment_cards_created(-deleted),
        )
        dialog.exec()

    def get_config(self) -> AnkiMinerConfig:
        """Get current configuration.

        Returns:
            Current configuration
        """
        return self.config

    def update_config(self, config: AnkiMinerConfig) -> None:
        """Update configuration, save to disk, and propagate to tabs.

        Every path bumps ``config_version`` and emits the post-save committed
        config so in-flight backfill plans stamped with an older version abort.

        Args:
            config: New configuration.
        """
        committed_config = replace(
            config,
            config_version=max(self.config.config_version, config.config_version) + 1,
        )
        try:
            GUIConfigManager.save_config(committed_config)
        except Exception as error:
            raise ConfigCommitError(ConfigCommitResult.pre_save_failure(error)) from error
        self.config = committed_config
        refresh_error: Exception | None = None
        try:
            # Re-seed the app-wide file-dialog mode so a toggled setting applies to
            # the very next dialog without restart (Issue #100).
            file_dialogs.set_use_native(committed_config.use_native_file_dialogs)
            # Rebuild config-bound services so AnkiConnect URL/port edits take
            # effect: validation and the undo-delete AnkiService were frozen to the
            # startup config and would otherwise keep hitting the old endpoint.
            self._build_config_bound_services()
        except Exception as error:
            refresh_error = error
        try:
            self.config_refreshed.emit(committed_config)
        except Exception as error:
            if refresh_error is None:
                refresh_error = error
        if refresh_error is not None:
            raise ConfigCommitError(ConfigCommitResult.post_save_failure(refresh_error)) from refresh_error

    def _build_config_bound_services(self) -> None:
        """(Re)create services bound to the current ``self.config``.

        Called once from ``__init__`` and again from every ``update_config``.
        ``_anki_service`` is the single instance the undo-delete callback in
        ``_on_processing_result`` reuses; ``validation_service`` backs the
        validation worker. Both must reflect the live config so an AnkiConnect
        URL change reaches Undo. The callback dereferences ``self._anki_service``
        lazily, so replacing the attribute here suffices — no stale closure
        captures the old service.
        """
        self.validation_service = ValidationService(self.config)
        # A corrupted anki_fields (missing a required key) makes AnkiService's
        # constructor raise ValueError. This runs inside __init__ and every
        # update_config (a Qt slot), so an unguarded raise is fatal. Guard it —
        # mirror AnkiProbeController — and leave _anki_service None; the Undo
        # callback re-checks for None and surfaces a clear error.
        self._anki_service: AnkiService | None = None
        try:
            self._anki_service = AnkiService(self.config)
        except ValueError as exc:
            logger.warning("Cannot build AnkiService (invalid anki_fields): %s", exc)
            if hasattr(self, "status_bar"):
                self.status_bar.set_operation(
                    self.tr("Anki note-type fields are misconfigured; check Settings."), "error"
                )

    def release_dictionary_resources(self) -> bool:
        """Ask every tab to release cached dictionary handles.

        Used by the Settings → Remove dictionary flow to drop SQLite handles
        before ``rmtree`` (Issue #30, Win11 file-lock). Returns ``False`` if
        prewarm is running or any tab refused because mining or card backfill
        is using indexed resources, so the caller can surface a clear message
        instead of silently failing.
        """
        if still_running(self.background_tasks.prewarm_worker):
            return False
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            release = getattr(tab, "release_dictionary_resources", None)
            if callable(release) and not release():
                return False
        return True

    def closeEvent(self, event) -> None:
        """Handle window close event.

        Delegates the shutdown join policy to the background-task controller:
        every owned and tab-owned worker is joined with a bounded grace, and
        workers that outlive it defer the close (controller hides the window,
        polls, then saves + quits) instead of being abandoned to Qt teardown.

        Args:
            event: Close event
        """
        # Flush a pending Settings auto-save FIRST. Ordering is load-bearing:
        # background_tasks.shutdown below fans out to SettingsTab.shutdown,
        # which stops debounce scheduling and begins worker teardown; persist
        # edits while the Settings tab is still fully active.
        # The deferred-close path also returns before the save_config at the
        # bottom, so this is the only spot both close paths pass through.
        # Committing routes through config_changed → update_config, which
        # writes gui_config.json and refreshes self.config for both the
        # immediate save below and the deferred _poll_deferred_close save.
        settings_idx = self._settings_tab_index()
        if settings_idx >= 0:
            flush = getattr(self.tabs.widget(settings_idx), "flush_pending_settings", None)
            if callable(flush):
                flush()

        # Stop the main-thread stall watchdog so its monitor thread and
        # heartbeat timer don't outlive shutdown. The monitor is daemon=True as
        # a backstop, but stopping it cleanly avoids a stray WARNING if a worker
        # join briefly blocks the GUI thread during close. Guarded: it may be
        # absent in tests/headless paths that never ran app.main()'s installer.
        watchdog = getattr(self, "_stall_watchdog", None)
        if watchdog is not None:
            watchdog.stop()

        # Stop the one-second task ticker for the same reason, and before the
        # deferred-close path can return: nothing should be repainting a status
        # strip while workers are being joined.
        self.task_registry.shutdown()

        laggards = self.background_tasks.shutdown(self.tabs)
        if laggards:
            self.background_tasks.defer_close(event, laggards)
            return

        # Release persistent per-tab processor dict handles before accepting
        # the close so SQLite connections are freed deterministically rather
        # than at Python GC.  Safe here: all workers are joined above so no
        # live thread is reading through these handles (OVH-061 / Issue #30).
        self.release_dictionary_resources()

        # Save configuration before closing
        try:
            GUIConfigManager.save_config(self.config)
        finally:
            event.accept()

    def _on_system_status_clicked(self) -> None:
        """Handle system status indicator click."""
        # Trigger system validation
        self._run_validation()

    def _run_validation(self) -> None:
        """Run system validation in background thread."""
        # The controller declines when a validation run is already in flight.
        # The current (config-bound) service is passed per call so the rebuild
        # in _build_config_bound_services reaches the next run.
        if not self.background_tasks.start_validation(self.validation_service):
            self.status_bar.set_operation(self.tr("Validation already running"), "info")
            return
        self.status_bar.set_operation(self.tr("Running system validation..."), "info")

    def _on_validation_finished(self, result: ValidationResult) -> None:
        """Handle validation worker completion.

        Args:
            result: Validation result from worker
        """
        # Emit through presenter for main window to handle
        self.presenter.show_validation_result(result)

    def _on_validation_error(self, error_message: str) -> None:
        """Handle validation worker error.

        Args:
            error_message: Error message from worker
        """
        silent = self._validation_silent
        self._validation_silent = False

        self.status_bar.set_operation(tr_format(self.tr("Validation error: %1"), error_message), "error")
        if not silent:
            QMessageBox.critical(self, self.tr("Validation Error"), error_message)

    def _maybe_repair_legacy_frequency_source_name(self) -> None:
        """One-time: repair the collapsed "source" label on the legacy source.

        Idempotent and self-guarded on the stored name; fixes a reimport bug
        that collapsed the ``legacy-frequency`` source's display name.
        """
        from anki_miner.services.frequency.legacy_migration import repair_legacy_frequency_source_name

        repair_legacy_frequency_source_name(self.config)

    def _maybe_migrate_legacy_pitch(self) -> None:
        """One-time: fold a legacy single pitch_accent.csv into the pitch chain.

        Synchronous (CSV→sqlite is fast and one-time, so no background worker).
        No-ops once migrated. Persists via ``update_config`` — NOT a bare
        ``GUIConfigManager.save_config`` — so the live session picks the chain
        up immediately (``self.config`` swap + config_version bump +
        ``config_refreshed`` emit); a bare save would leave pitch inactive
        until the next launch.
        """
        from anki_miner.services.pitch_accent.legacy_migration import migrate_legacy_pitch_csv

        migrated = migrate_legacy_pitch_csv(self.config)
        if migrated is not None:
            self.update_config(migrated)
            logger.info("Migrated legacy pitch_accent.csv into pitch/legacy-pitch")

    def _maybe_migrate_jmdict(self) -> None:
        """One-time: migrate legacy JMdict XML into a SQLite index in the background."""
        if self.background_tasks.maybe_migrate_jmdict(self.config):
            self.status_bar.set_operation(self.tr("Migrating JMdict to SQLite…"), "info")

    def _on_jmdict_migration_finished(self, dict_id: str, meta: dict) -> None:
        """Notify tabs that they need to rebuild any cached DefinitionService.

        We don't mutate config here — the chain entry is already correct (it
        was the trigger). We re-emit so YouTubeTab (and any future caching
        tab) rebuilds its processor and picks up the newly-available index.
        """
        logger.info("JMdict migration complete: %s (%s entries)", dict_id, meta.get("entry_count"))
        self.status_bar.set_operation(
            tr_format(self.tr("JMdict ready (%1 entries)"), f"{meta.get('entry_count', 0):,}"),
            "info",
        )
        self.config_refreshed.emit(self.config)

    def _check_for_updates(self) -> None:
        """Check for application updates in background thread."""
        self.background_tasks.check_for_updates()

    def _maybe_start_ytdlp_update(self) -> None:
        """Kick off the throttled yt-dlp self-update (deferred so the window paints first).

        Extracted from __init__ so the unit-test harness has a single seam to no-op
        (like _check_for_updates / _maybe_migrate_jmdict). Without that seam, every
        real-MainWindow test spawned a live YtdlpUpdateWorker QThread running a blocking
        `yt-dlp --version` subprocess; the autouse _drain_qt_deletes flush could then
        destroy the running QThread mid-subprocess -> SIGABRT. Identical runtime behavior.
        """
        if self.config.auto_update_ytdlp:
            QTimer.singleShot(0, lambda: self.background_tasks.start_ytdlp_update(self.config, force=False))

    def _on_update_check_result(self, info: object) -> None:
        """Handle update check result.

        Args:
            info: An :class:`~anki_miner.services.update_checker.UpdateInfo`
                when a newer release is available, or ``None`` when there is
                no update / the check failed.
        """
        from anki_miner.gui.widgets.update_banner import UpdateBanner
        from anki_miner.services.update_checker import UpdateInfo

        if not isinstance(info, UpdateInfo):
            return

        # Honor the user's "skip this version" choice.
        if info.version == self.config.skipped_update_version:
            return

        # The banner is a singleton: create it once, then reuse it on every
        # subsequent check result via update_info() (property mutation) rather
        # than reconstructing it. Tearing it down with setParent(None) +
        # deleteLater() would race in-flight Qt callbacks. The skip button only
        # hides the banner; it never deleteLater()s it.
        if self._update_banner is None:
            banner = UpdateBanner(info, self)
            banner.skip_requested.connect(self._on_skip_update_requested)
            # Insert banner after header (index 1).
            self.central_layout.insertWidget(1, banner)
            self._update_banner = banner
        else:
            self._update_banner.update_info(info)
            self._update_banner.setVisible(True)

    def _on_ytdlp_update_result(self, result: object) -> None:
        """Handle a yt-dlp background-update result.

        Auto path is no-nag: log always; on ``installed`` show a brief status-bar
        line. No dialog here — the manual path's dialog lives in SettingsTab
        (:meth:`SettingsTab.set_ytdlp_status_from_result`), driven off the same
        signal but gated on a user-initiated click.
        """
        action = getattr(result, "action", "")
        message = getattr(result, "message", "") or ""
        logger.info("yt-dlp update result: action=%s %s", action, message)
        if action == "installed" and message:
            self.status_bar.showMessage(message, 5000)

    def _on_skip_update_requested(self, version: str) -> None:
        """Persist the skipped version and hide the banner.

        Args:
            version: Version string the user chose to skip.
        """
        self.update_config(replace(self.config, skipped_update_version=version))
        if self._update_banner is not None:
            self._update_banner.setVisible(False)

    def _on_theme_changed(self, theme_name: str) -> None:
        """Handle theme change from header widget.

        Args:
            theme_name: Name of the new theme
        """
        # Apply new stylesheet and palette
        app = QApplication.instance()
        if isinstance(app, QApplication):
            Theme.apply_to_app(app, theme_name)

        # Update header to reflect current theme
        self.header.update_theme_selector()

        # Persist active theme to gui_config.json so it survives restart.
        if theme_name != self.config.theme:
            self.update_config(replace(self.config, theme=theme_name))
