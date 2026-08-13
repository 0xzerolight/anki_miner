"""Main window for Anki Miner GUI."""

import logging
import sys
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

from PyQt6.QtCore import QEvent, QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QAction,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QShortcut,
    QShowEvent,
    QWindowStateChangeEvent,
)
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
from anki_miner.diagnostics.bundle import BundleResult, default_bundle_name, write_diagnostics_bundle
from anki_miner.diagnostics.environment import (
    EnvironmentSnapshot,
    collect_environment,
    format_environment_lines,
    format_health_lines,
)
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
from anki_miner.gui.utils import file_dialogs, queue_state_store, session_state
from anki_miner.gui.utils.config_commit import ConfigCommitError, ConfigCommitResult
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.utils.dialog_paths import resolve_start_dir
from anki_miner.gui.utils.keyboard_shortcuts import (
    HELP_SEQUENCE,
    SETTINGS_SEQUENCE,
    TAB_SEQUENCE_TEMPLATE,
)
from anki_miner.gui.utils.qt_helpers import fit_window_minimum, widget_alive
from anki_miner.gui.utils.run_off_thread import run_off_thread, still_running
from anki_miner.gui.widgets.base import ScreenIssue, ScreenIssueHost, install_animated_tab_bar
from anki_miner.gui.widgets.dialogs.results_dialog import ResultsDialog
from anki_miner.gui.widgets.dialogs.system_health_window import (
    HEALTH_KEYS,
    HEALTH_OK,
    HEALTH_UNKNOWN,
    HEALTH_WARN,
    HealthReport,
    SystemHealthWindow,
)
from anki_miner.gui.widgets.header_widget import HeaderWidget
from anki_miner.gui.widgets.mini_job_monitor import MiniJobMonitor
from anki_miner.gui.widgets.status_bar_widget import StatusBarWidget
from anki_miner.models import ProcessingResult, ValidationResult
from anki_miner.services import ShortcutResult, ShortcutService, ValidationService
from anki_miner.services.anki_service import AnkiService
from anki_miner.utils.bundled_binary import frozen_state
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from anki_miner.gui.capabilities import CapabilityTarget
    from anki_miner.gui.widgets.dialogs.resource_download_dialog import ResourceDownloadSession
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizardOutcome

logger = logging.getLogger(__name__)


def open_log_folder(log_path: Path) -> None:
    """Open the parent directory of *log_path* in the system file manager."""
    from PyQt6.QtCore import QUrl
    from PyQt6.QtGui import QDesktopServices

    log_folder = Path(log_path).parent
    log_folder.mkdir(parents=True, exist_ok=True)
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_folder)))


class MainWindow(ScreenIssueHost, QMainWindow):
    """Main application window for Anki Miner.

    This window provides a tabbed interface for:
    - Video (container: Single episode / Batch folder / YouTube sub-tabs)
    - Deck Builder (corpus-driven deck assembly)
    - Audiobooks (audio + subtitle pair queue)
    - Reading (container: Manga / Novels sub-tabs)
    - Analytics (mining statistics dashboard)
    - Utilities (container: Generate / Retime / Condense / Card Backfill)
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
        # Optional startup work runs once per launch, behind first-run setup.
        # Set at close so a wizard exiting during shutdown starts nothing.
        self._post_setup_boot_started = False
        self._stale_dict_prompt_handled = False
        # Set the first time closeEvent persists geometry + route. A deferred
        # close runs closeEvent again after the window has been hidden, and the
        # hidden window's geometry is not what the user left behind.
        self._session_state_saved = False
        self._screen_change_tracked = False
        # Set once closeEvent has committed to quitting — see is_shutting_down.
        self._close_committed = False

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
        self.task_registry.reveal_requested.connect(self._on_task_activated)

        # The live recommended-resource run, if any. Retained here rather than
        # Qt-parented: it outlives its own window, which the user can hide.
        self._resource_download_session: ResourceDownloadSession | None = None

        # Config-bound services (validation + the AnkiService shared across undo
        # callbacks). Rebuilt on every config change via update_config — see
        # _build_config_bound_services — so an AnkiConnect URL/port edit reaches
        # the next Undo delete instead of the stale startup endpoint.
        self._build_config_bound_services()
        self._validation_silent = True

        # Readiness facts live here, not on the System Health screen, so a
        # result arriving while that screen is closed is not lost and a reopened
        # window is immediately correct (D26). The window itself is built the
        # first time it is asked for.
        self._health_report = HealthReport.unknown()
        self._last_logged_health_states: dict[str, str] | None = None
        self._system_health_window: SystemHealthWindow | None = None
        self._diagnostics_export_running = False

        # The floating job monitor (D53). Built the first time it is asked for,
        # and read-only: it observes self.task_registry and holds nothing else.
        self._mini_job_monitor: MiniJobMonitor | None = None

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
            self._run_optional_boot_step("environment snapshot", self._start_environment_snapshot)

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

        if not self.config.first_run_shortcut_done:
            QTimer.singleShot(0, self._maybe_create_shortcut_on_first_run)

        # First-run setup goes FIRST, and everything optional waits behind it.
        # Boot used to start the JMdict migration and then have the wizard
        # cancel it two lines later, so the very first launch spent its startup
        # doing work it immediately threw away — and the wizard's own Resources
        # download writes into the same dictionary slot the migration targets.
        if not self.config.first_run_setup_done:
            QTimer.singleShot(0, self._maybe_offer_first_run_setup)
        else:
            self._start_post_setup_boot_once()

    def _start_post_setup_boot_once(self) -> None:
        """Start every optional startup job, at most once per launch.

        The single choke point every first-run exit path funnels through —
        Finish, Skip, Escape, the window close, an exception, and the mutation
        guard refusing. Guarded rather than ordered, because those paths cannot
        all be made to happen exactly once each: the offer can be refused and
        re-offered, and a refusal must not consume the one-time work either.

        Deliberately not a state machine. It starts the same jobs ``commit_boot``
        always started, in the same order; the only new thing is that it can be
        called from more than one place and still run once.
        """
        if self._post_setup_boot_started:
            return
        self._post_setup_boot_started = True

        self._validation_silent = True
        self._run_optional_boot_step("startup validation", self._run_validation)
        if self.config.check_for_updates:
            self._run_optional_boot_step("update check", self._check_for_updates)
        self._run_optional_boot_step("JMdict migration", self._maybe_migrate_jmdict)
        self._run_optional_boot_step("yt-dlp update", self._maybe_start_ytdlp_update)
        QTimer.singleShot(0, self._maybe_prompt_stale_dictionaries)
        QTimer.singleShot(0, self._start_prewarm)

    def _start_prewarm(self) -> None:
        """Warm the shared MeCab tagger and the dictionary chain off-thread.

        The first Mine otherwise builds both on the GUI thread — ``fugashi``
        plus every installed dictionary's sqlite index — and freezes for
        seconds. Best-effort: clicking Mine before it finishes simply takes the
        cold path. ``BackgroundTaskController`` holds the reference so the
        QThread is not collected mid-run and shutdown can join it.

        Scheduled on the next event-loop turn, so it never blocks the first
        paint, and from the one-shot boot step, so it never runs *during* the
        first-run wizard: a zero timer fires inside a modal dialog's nested
        event loop, and this reads the dictionary slot that wizard replaces.
        """
        from anki_miner.gui.workers.prewarm_worker import PrewarmWorker

        worker = PrewarmWorker(self.get_config())
        self.background_tasks.set_prewarm(worker)
        worker.start()

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
        # Before anything is restored: the floor decides what a restore can
        # land on, so a screen-sized cap has to be in place first.
        self._apply_screen_fit()

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

        # Whole-window issues (system checks, dictionary mutation refusals) sit
        # under the header, above every tab — the same slot the update banner
        # uses, because they are statements about the app rather than the page
        # (D24).
        self.install_issue_banner(self.central_layout, 1)

        # Create tab widget
        self.tabs = QTabWidget()
        install_animated_tab_bar(self.tabs)
        self.central_layout.addWidget(self.tabs)

        central_widget.setLayout(self.central_layout)
        self.setCentralWidget(central_widget)

        # Enhanced status bar
        self.status_bar = StatusBarWidget()
        self.status_bar.system_status_clicked.connect(self._on_system_status_clicked)
        self.status_bar.task_activated.connect(self._on_task_activated)
        self.status_bar.mini_monitor_requested.connect(self.open_mini_job_monitor)
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
            self.tr("Navigate between Video, Deck Builder, Audiobooks, Reading, Analytics, Utilities, and Settings")
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

        setup_wizard_action = tools_menu.addAction(self.tr("Setup Wizard..."))
        assert setup_wizard_action is not None
        setup_wizard_action.triggered.connect(self._run_setup_wizard_tool)

        restyle_action = tools_menu.addAction(self.tr("Restyle Mined Cards..."))
        assert restyle_action is not None
        restyle_action.triggered.connect(self._restyle_mined_cards)

        # Help menu
        help_menu = menu_bar.addMenu(self.tr("&Help"))
        assert help_menu is not None

        # No shortcut: About is a credits card, not help. F1 belongs to the
        # Usage Guide (D48-B).
        about_action = help_menu.addAction(self.tr("About Anki Miner"))
        assert about_action is not None
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

        export_diagnostics_action = help_menu.addAction(self.tr("Export Diagnostics…"))
        assert export_diagnostics_action is not None
        self.export_diagnostics_action = export_diagnostics_action
        export_diagnostics_action.setToolTip(self.tr("Save a zip with logs and system details for a bug report"))
        export_diagnostics_action.triggered.connect(self._export_diagnostics)

        # Usage Guide -- a top-level menu-bar button, not a dropdown, placed
        # after Help. F1 is help everywhere, and "which screen does this?" is
        # the help question this application can actually answer (D48-B). A
        # menu-less top-level QAction is silently dropped from native menu bars
        # (macOS, Linux global menu), so those platforms get a one-action menu
        # instead.
        if menu_bar.isNativeMenuBar():
            guide_menu = menu_bar.addMenu(self.tr("Usage Guide"))
            assert guide_menu is not None
            guide_action = guide_menu.addAction(self.tr("Open Usage Guide..."))
            assert guide_action is not None
            guide_action.setMenuRole(QAction.MenuRole.NoRole)
        else:
            guide_action = menu_bar.addAction(self.tr("Usage Guide"))
            assert guide_action is not None
        guide_action.setShortcut(QKeySequence(HELP_SEQUENCE))
        guide_action.triggered.connect(self._run_capability_browser_tool)
        self.usage_guide_action = guide_action

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
        # Settings (Ctrl+,). The only global binding left, and the only one that
        # never meant something else first. Ctrl+T (new tab) and Ctrl+Shift+V
        # (paste as plain text) were dropped under D48-B: both collide with a
        # binding every desktop already owns, and both had a visible control
        # doing the same job -- the header's favourites combo, and Settings'
        # validation button, which still owns the validation run.
        settings_shortcut = QShortcut(QKeySequence(SETTINGS_SEQUENCE), self)
        settings_shortcut.activated.connect(self._open_settings)

    def setup_tab_shortcuts(self) -> None:
        """Create one Ctrl+N shortcut per registered tab, driven by the live tab count.

        Called by app.py after all tabs have been registered so the count is
        final.  Creating these in :meth:`_setup_shortcuts` (which runs in
        ``__init__``, before app.py adds any tabs) would under-count and leave
        the later tabs unreachable.
        """
        for i in range(1, self.tabs.count() + 1):
            shortcut = QShortcut(QKeySequence(TAB_SEQUENCE_TEMPLATE.format(number=i)), self)
            shortcut.activated.connect(lambda idx=i - 1: self._switch_to_tab(idx))

    def _switch_to_tab(self, index: int) -> None:
        """Switch to tab at given index.

        Args:
            index: Tab index (0-based)
        """
        if 0 <= index < self.tabs.count():
            self.tabs.setCurrentIndex(index)

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

    def _acquire_resource_download_mutation(self) -> tuple[AnkiMinerConfig, Callable[[], None]] | None:
        """Hold Settings' dictionary mutation token for one native worker run."""
        settings_idx = self._settings_tab_index()
        if settings_idx < 0:
            return self.config, lambda: None
        settings_tab = self.tabs.widget(settings_idx)
        preflight = getattr(settings_tab, "commit_pending_settings_for_mutation", None)
        panel = getattr(settings_tab, "dictionary_panel", None)
        if not callable(preflight) or panel is None:
            return self.config, lambda: None
        if not preflight():
            return None
        token = panel.hold_mutation("resource-download")
        return self.config, lambda: panel.release(token)

    def _report_shortcut_failure(self, details: str) -> None:
        """One place for the desktop-shortcut failure sentence (D24)."""
        self.show_screen_issue(
            ScreenIssue(summary=self.tr("The desktop shortcut could not be created."), details=details)
        )

    def prepare_dictionary_mutation(self) -> bool:
        """Stop startup JMdict migration or show the shared refusal dialog."""
        if self.background_tasks.prepare_dictionary_mutation():
            return True
        self.show_screen_issue(
            ScreenIssue(
                summary=self.tr("The startup JMdict migration is still stopping. Wait for it to finish and try again.")
            )
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

    def _current_main_tab_key(self) -> str | None:
        """The stable key of the main tab on show, by class name; ``None`` if unmapped."""
        current = self.tabs.currentWidget()
        if current is None:
            return None
        class_name = type(current).__name__
        for key, registered in self._MAIN_TAB_CLASSES.items():
            if registered == class_name:
                return key
        return None

    def _current_subtab_keys(self) -> dict[str, str]:
        """Every container's current sub-tab, not only the visible one.

        Saving just the front container would erase where Reading was left the
        moment the user switched to Tools before quitting.
        """
        keys: dict[str, str] = {}
        for key in self._MAIN_TAB_CLASSES:
            index = self._main_tab_index(key)
            if index < 0:
                continue
            current = getattr(self.tabs.widget(index), "current_subtab_key", None)
            if not callable(current):
                continue
            subtab = current()
            if subtab:
                keys[key] = subtab
        return keys

    def restore_session_state(self) -> None:
        """Reopen at the geometry and route the last session ended on (D7).

        Called by ``app.compose_main_window`` once every tab is registered —
        the route is addressed by stable key, so the containers have to exist —
        and before ``show()``, so the window is never painted at one size and
        then moved.

        ``restoreGeometry`` is the authority on a stored blob: it restores the
        maximised/full-screen state along with the normal geometry and already
        relocates a window saved on a monitor that no longer exists back onto an
        available screen. Nothing calls ``setGeometry`` after it succeeds — that
        would un-maximise the window it just restored. The centred default is
        only for an absent or unusable blob.

        ``_apply_screen_fit`` runs afterwards because a blob written on a bigger
        monitor comes back bigger than this one; it leaves a maximised or
        full-screen window alone, so it cannot undo the restore above.

        Scroll positions are not restored, here or anywhere: nothing writes them,
        so every restored page opens at the top.
        """
        blob = session_state.load_geometry()
        if blob is None or not self.restoreGeometry(blob):
            self._apply_default_geometry()
        self._apply_screen_fit()

        main_tab, subtabs = session_state.load_route()
        # Sub-tabs first: switching the main tab afterwards lands on a container
        # that is already showing the right inner page.
        for container_key, subtab_key in subtabs.items():
            index = self._main_tab_index(container_key)
            if index < 0:
                continue
            open_subtab = getattr(self.tabs.widget(index), "open_subtab", None)
            if callable(open_subtab):
                open_subtab(subtab_key)
        if main_tab:
            index = self._main_tab_index(main_tab)
            if index >= 0:
                self.tabs.setCurrentIndex(index)

    def _apply_screen_fit(self) -> None:
        """Keep the window inside the screen it is on, minimum included.

        The 1024x768 minimum is a design contract written in logical pixels, and
        on Windows at the 150% scaling it recommends for a 1080p laptop the work
        area is only ~1280x672. A minimum taller than the screen is enforced by
        Windows on every sizing operation, so the restore from maximised lands
        back on a screen-filling rect and the borders stop dragging: the window
        reads as stuck maximised, and because ``saveGeometry`` persists the
        maximised state it comes back that way at every launch.

        Two halves, both needed. The cap (:func:`fit_window_minimum`) is what
        makes a smaller size *legal*; shrinking an already-oversized window is
        what makes leaving the maximised state visibly do something, because
        Windows hands back the pre-maximise rect, which is oversized too.

        A maximised or full-screen window is left exactly as it is — that state
        is the user's, and touching its geometry would cancel it.
        """
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        frame = self.frameGeometry()
        # Both the cap and the shrink are budgeted against the *client* area,
        # because that is what ``setMinimumSize`` and ``resize`` address while
        # it is the frame that has to fit the work area. Zero until the native
        # window exists, which is why this is re-run from ``showEvent``.
        budget = available.size() - (frame.size() - self.size())
        self.setMinimumSize(fit_window_minimum(QSize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT), budget))

        if self.windowState() & (Qt.WindowState.WindowMaximized | Qt.WindowState.WindowFullScreen):
            return
        if available.contains(frame):
            return
        self.resize(fit_window_minimum(self.size(), budget))
        # move() positions a top-level window by its frame, which is what has to
        # land inside the work area.
        frame = self.frameGeometry()
        frame.moveLeft(max(available.left(), min(frame.left(), available.right() - frame.width() + 1)))
        frame.moveTop(max(available.top(), min(frame.top(), available.bottom() - frame.height() + 1)))
        self.move(frame.topLeft())

    def changeEvent(self, a0: QEvent | None) -> None:  # noqa: N802 - Qt override
        """Refit the window when it stops being maximised.

        The restore hands back the geometry the window had before it was
        maximised, which on an affected screen was already oversized. Without
        this the restore button appears to do nothing.

        Deferred by one event-loop turn because Qt delivers the state change
        *before* it applies the restored normal geometry — refitting inline
        would be overwritten a moment later (measured, offscreen and X11).
        """
        super().changeEvent(a0)
        if a0 is None or a0.type() != QEvent.Type.WindowStateChange:
            return
        was_maximised = isinstance(a0, QWindowStateChangeEvent) and bool(
            a0.oldState() & (Qt.WindowState.WindowMaximized | Qt.WindowState.WindowFullScreen)
        )
        if was_maximised and not self.is_shutting_down():
            QTimer.singleShot(0, self._deferred_screen_fit)

    def _deferred_screen_fit(self) -> None:
        """``_apply_screen_fit`` for a timer that can outlive the window."""
        if widget_alive(self):
            self._apply_screen_fit()

    def showEvent(self, a0: QShowEvent | None) -> None:  # noqa: N802 - Qt override
        """Track screen changes once the native window exists.

        ``windowHandle()`` is ``None`` until the window is created, so the
        connection cannot be made in ``_setup_ui``. Dragging the window from a
        4K monitor onto a 1366x768 laptop panel is the same bug arriving late.

        The refit is repeated here because the window frame only has a size once
        the platform window exists, and the frame is what has to fit.
        """
        super().showEvent(a0)
        handle = self.windowHandle()
        if handle is not None and not self._screen_change_tracked:
            self._screen_change_tracked = True
            handle.screenChanged.connect(lambda _screen: self._apply_screen_fit())
        self._apply_screen_fit()

    def _apply_default_geometry(self) -> None:
        """1280x800 centred on the primary screen — the no-saved-state default.

        Clamped to the screen's available area so the default cannot start
        partly off a small display. ``setMinimumSize`` no longer defeats the
        clamp: ``_apply_screen_fit`` has already capped the minimum at the
        screen.
        """
        screen = QApplication.primaryScreen()
        if screen is None:
            self.resize(WINDOW_DEFAULT_WIDTH, WINDOW_DEFAULT_HEIGHT)
            return
        available = screen.availableGeometry()
        rect = QRect(
            0,
            0,
            min(WINDOW_DEFAULT_WIDTH, available.width()),
            min(WINDOW_DEFAULT_HEIGHT, available.height()),
        )
        rect.moveCenter(available.center())
        self.setGeometry(rect)

    def _save_session_state(self) -> None:
        """Persist geometry + route exactly once, at the top of closeEvent.

        Best-effort by construction: ``session_state`` swallows and logs its own
        failures, and this is wrapped anyway so nothing about a convenience can
        stop the window from closing.
        """
        if self._session_state_saved:
            return
        self._session_state_saved = True
        try:
            session_state.save_geometry(self.saveGeometry())
            session_state.save_route(self._current_main_tab_key(), self._current_subtab_keys())
        except Exception:
            logger.exception("Could not save the UI session state")
        self.save_queue_snapshots()

    def iter_queue_screens(self) -> list[QWidget]:
        """Every screen that can describe its queue durably (D16-C).

        Discovered by capability rather than by a hand-kept list: a queue can
        live inside a container tab (Video, Reading), and a list that had to be
        edited whenever a sub-tab moved would silently stop saving one.
        """
        return [widget for widget in self.findChildren(QWidget) if getattr(widget, "QUEUE_STATE_KEY", None)]

    def save_queue_snapshots(self) -> None:
        """Write every screen's queue so quitting does not discard it (D16-C).

        Called from the top of ``closeEvent``, before anything is joined or
        hidden — a queue read after teardown has begun is a queue that may
        already have been emptied. Best-effort per screen: one screen that
        cannot describe itself must not stop the others being saved, and none of
        it may stop the window closing.
        """
        for screen in self.iter_queue_screens():
            try:
                snapshot = screen.queue_snapshot()  # type: ignore[attr-defined]
            except Exception:
                logger.exception("Could not read the queue on %s", type(screen).__name__)
                continue
            queue_state_store.save(snapshot)

    def restore_queue_snapshots(self) -> int:
        """Refill every screen from its stored queue; return the rows restored.

        Nothing is started. A row that was mid-run comes back saying it was
        interrupted, and only an explicit later action of the user's turns it
        back into work.
        """
        restored = 0
        for screen in self.iter_queue_screens():
            key = getattr(screen, "QUEUE_STATE_KEY", "")
            snapshot = queue_state_store.load(key)
            if snapshot is None:
                continue
            try:
                restored += screen.restore_queue_snapshot(snapshot)  # type: ignore[attr-defined]
            except Exception:
                logger.exception("Could not restore the queue on %s", type(screen).__name__)
        return restored

    def reveal_capability(self, target: "CapabilityTarget") -> None:
        """Bring the tab that hosts ``target`` to the front (and its sub-tab).

        Called by the Usage Guide browser. No-ops silently if the tab can't be
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

        A run owning a transient window of its own is taken back to *that*
        window: a hidden resource download has nothing to show on the
        Dictionaries page it would otherwise navigate to.
        """
        snapshot = self.task_registry.snapshot(task_id)
        if snapshot is None:
            return
        session = self._resource_download_session
        if session is not None and getattr(session, "task_id", None) == task_id:
            session.reveal()
            return
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

    def _export_diagnostics(self) -> None:
        """Ask where to write a diagnostics bundle without blocking the GUI."""
        if self._diagnostics_export_running:
            return
        default_path = Path(resolve_start_dir(None, file_mode=True)) / default_bundle_name()
        file_dialogs.pick_save_file(
            self,
            self.tr("Export Diagnostics"),
            str(default_path),
            self.tr("Zip Archives (*.zip);;All Files (*)"),
            on_done=self._on_diagnostics_target_picked,
        )

    def _on_diagnostics_target_picked(self, path_str: str) -> None:
        """Snapshot GUI-owned state, then collect and write on a worker.

        Guards the flag here rather than before the picker: a cancelled picker
        never calls back, so an early set would strand both entry points
        disabled. Two menu invocations can therefore open two pickers, and this
        is what keeps the second one from starting a concurrent export.
        """
        if not path_str or self._diagnostics_export_running:
            return

        target = Path(path_str)
        config = self.config
        health_report = self._health_report
        platform_name = QGuiApplication.platformName()

        def work() -> BundleResult:
            snapshot = collect_environment(config, platform_name=platform_name)
            rows = []
            for key in HEALTH_KEYS:
                check = health_report.get(key)
                rows.append((check.key, check.state, check.detail, check.checked_at))
            return write_diagnostics_bundle(
                target,
                config=config,
                snapshot=snapshot,
                health_lines=format_health_lines(rows),
            )

        self._diagnostics_export_running = True
        self.export_diagnostics_action.setEnabled(False)
        if self._system_health_window is not None:
            self._system_health_window.set_export_enabled(False)
        run_off_thread(
            self,
            work,
            self._on_diagnostics_done,
            self._on_diagnostics_error,
            on_finished=self._on_diagnostics_finished,
        )

    def _on_diagnostics_done(self, result: object) -> None:
        """Report a completed bundle without exposing its absolute path."""
        bundle = cast(BundleResult, result)
        banner = self.issue_banner()
        if banner is not None:
            issue = banner.current_issue()
            if issue is not None and issue.action_id == "diagnostics.export-retry":
                self.clear_screen_issue()
        self.status_bar.set_operation(
            tr_format(self.tr("Diagnostics written to %1"), bundle.path.name),
            "info",
        )

    def _on_diagnostics_error(self, message: str) -> None:
        """Keep a recoverable export failure on the main screen."""
        self.show_screen_issue(
            ScreenIssue(
                summary=self.tr("Diagnostics could not be exported."),
                details=message,
                action_id="diagnostics.export-retry",
                action_text=self.tr("Retry"),
            ),
            action=self._export_diagnostics,
        )

    def _on_diagnostics_finished(self) -> None:
        """Restore every diagnostics entry point after either outcome."""
        self._diagnostics_export_running = False
        self.export_diagnostics_action.setEnabled(True)
        if self._system_health_window is not None:
            self._system_health_window.set_export_enabled(True)

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
                self._report_shortcut_failure("")
                return
            body = "\n".join(value.messages) if value.messages else ""
            if value.success:
                QMessageBox.information(self, self.tr("Desktop Shortcut"), body or self.tr("Shortcut created."))
            else:
                self._report_shortcut_failure(value.error or "")

        def on_error(message: str) -> None:
            finish_attempt()
            logger.warning("Desktop shortcut attempt failed: %s", message)
            if show_result:
                self._report_shortcut_failure(message)

        try:
            run_off_thread(self, work, on_done, on_error, on_finished=finish_attempt)
        except Exception as exc:
            on_error(str(exc))

    def _download_recommended_resources(self) -> None:
        """Tools-menu handler: start the background recommended-resource run.

        Mining stays usable while several hundred megabytes transfer. Settings'
        dictionary mutation token stays held through native worker finish so the
        root cannot move under the captured import paths. The same-slot startup
        migration is stopped first.
        """
        from anki_miner.gui.widgets.dialogs.resource_download_dialog import start_resource_download

        if not self.prepare_dictionary_mutation():
            return
        self.background_tasks.cancel_jmdict_migration()
        session = start_resource_download(
            self,
            self.config,
            activate=self._activate_downloaded_resources,
            release_resources=self.release_dictionary_resources,
            acquire_mutation=self._acquire_resource_download_mutation,
            blocked=self._show_resource_download_blocked,
            task_registry=self.task_registry,
            adopt_worker=self.background_tasks.adopt_resource_download_worker,
        )
        if session is not None:
            # Retained here, not Qt-parented: the session outlives its window.
            self._resource_download_session = session
            self._clear_resource_download_issue()

    def _show_resource_download_blocked(self, message: str) -> None:
        """Keep recoverable resource contention on the main issue surface."""
        self.show_screen_issue(
            ScreenIssue(
                summary=message,
                action_id="resource-download.retry",
                action_text=self.tr("Retry"),
            ),
            action=self._download_recommended_resources,
        )

    def _clear_resource_download_issue(self) -> None:
        """Clear only contention reported by the recommended-resource run."""
        banner = self.issue_banner()
        if banner is not None:
            issue = banner.current_issue()
            if issue is not None and issue.action_id == "resource-download.retry":
                self.clear_screen_issue()

    def _activate_downloaded_resources(self, summary: object) -> "AnkiMinerConfig | None":
        """Switch downloaded resources on, or refuse without claiming success.

        Committing pending Settings is step 0 and aborts activation when it
        fails. It has to be: the download now runs in the background, so the
        user can have edited Settings while it did, and computing the new config
        from the base captured when the download started would silently revert
        that edit. The guard commits first, then this reads the *live*
        ``self.config``.
        """
        from anki_miner.gui.utils.resource_setup import apply_download_summary
        from anki_miner.gui.workers.resource_download_worker import ResourceDownloadSummary

        if not isinstance(summary, ResourceDownloadSummary) or not summary.succeeded:
            return None
        with self._dictionary_mutation_guard("resource-download") as ready:
            if not ready:
                return None
            # update_config (not from_settings) propagates via config_refreshed
            # to all tabs incl. Settings, and persists.
            try:
                new_config = apply_download_summary(self.config, summary)
            except ValueError as error:
                self.show_screen_issue(
                    ScreenIssue(
                        summary=self.tr(
                            "Downloaded resources were left inactive because their storage folder changed."
                        ),
                        details=str(error),
                        action_id="settings.dictionaries",
                        action_text=self.tr("Open Settings"),
                    ),
                    action=self._open_settings,
                )
                return None
            self.update_config(new_config)
            self._clear_resource_download_issue()
            return new_config

    def _run_capability_browser_tool(self) -> None:
        """Menu-bar handler: open the Usage Guide browser.

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
        """Merge live one-way flags, persist one wizard outcome, then act on it."""
        from anki_miner.gui.capabilities import CapabilityTarget

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
        # Strictly after the commit: the screen the user lands on rebuilds from
        # the config, and taking them there first would show them the setup they
        # just replaced.
        if outcome.open_video_mining:
            self.reveal_capability(CapabilityTarget("video", "single"))

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
            self.show_screen_issue(
                ScreenIssue(
                    summary=self.tr("Restyling cannot start: the Anki field mapping is not usable."),
                    details=str(exc),
                    action_id="settings.anki",
                    action_text=self.tr("Open Anki Settings"),
                ),
                action=lambda: self.reveal_capability(CapabilityTarget("settings", "anki")),
            )
            return
        self.status_bar.set_operation(self.tr("Restyling mined cards…"), "info")

        def on_progress(scanned: int, total: int) -> None:
            self.status_bar.set_operation(tr_format(self.tr("Restyling mined cards… %1/%2"), scanned, total), "info")

        def on_result(result: RestyleResult) -> None:
            if result.failed:
                self.status_bar.set_operation(self.tr("Restyle incomplete"), "error")
                self.show_screen_issue(
                    ScreenIssue(
                        summary=tr_format(
                            self.tr("%1 note update(s) were not confirmed; run Restyle again."),
                            result.failed,
                        ),
                        details=tr_format(
                            self.tr("Restyled %1 card(s). (%2 scanned; %3 already up to date.)"),
                            result.restyled,
                            result.scanned,
                            result.skipped_styled,
                        ),
                    )
                )
                return
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
            self.show_screen_issue(
                ScreenIssue(summary=self.tr("The mined cards could not be restyled."), details=message)
            )

        self.background_tasks.start_restyle_cards(service, self.config, on_progress, on_result, on_error)

    def _maybe_offer_first_run_setup(self) -> None:
        """Offer guided setup and consume the offer only on finish or Skip.

        Broadened (Task 3): the wizard is offered whenever the run hasn't been
        completed (``not first_run_setup_done``) — no longer gated on freq/pitch
        file presence, since the wizard's Resources step covers those. The
        wizard's returned partial config is always persisted. Dismissal leaves
        the offer unconsumed; failures are logged and re-offered next launch.

        Optional startup work is deferred behind this and released on *every*
        exit — including a mutation refusal and an exception — so an interrupted
        first run leaves the app in a normal booted state rather than one with
        no validation, no update check and no migration until the next launch.
        """
        from anki_miner.gui.widgets.dialogs.setup_wizard import run_setup_wizard

        # Re-entrancy / idempotency guard: never run twice, and never re-enter if
        # the 0ms timer fires inside a nested modal loop. Set before any work so a
        # re-entrant fire during the wizard's exec() bails out immediately.
        if self._first_run_setup_handled:
            return
        self._first_run_setup_handled = True

        try:
            with self._dictionary_mutation_guard("first-run-setup-wizard") as ready:
                if not ready:
                    self._first_run_setup_handled = False
                    return
                # No cancel_jmdict_migration here any more: boot no longer starts
                # the migration before this offer, which is the point of the
                # deferral. The re-runnable Tools entry still cancels, because
                # there a migration really can be in flight.
                try:
                    outcome = run_setup_wizard(self, self.config)
                except Exception:
                    logger.exception("Setup wizard failed")
                    return
                self._commit_setup_wizard_outcome(outcome, first_run_offer=True)
        finally:
            self._start_post_setup_boot_once()

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
        self.presenter.run_details_signal.connect(self._on_run_details)

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
                errors = result.get_errors()
                warnings = result.get_warnings()
                component_counts = Counter(issue.component for issue in result.issues)
                # Severity drives the level: a missing optional tool (alass,
                # yt-dlp) must not read like AnkiConnect being down. This used
                # to be an unconditional WARNING, so the two were indistinguishable.
                logger.log(
                    logging.ERROR if errors else logging.WARNING,
                    "Startup validation completed: issues=%d errors=%d warnings=%d components=%s",
                    len(result.issues),
                    len(errors),
                    len(warnings),
                    ",".join(f"{name}={count}" for name, count in sorted(component_counts.items())),
                )

        # Update system status indicators
        ankiconnect_ok = all(issue.component != "AnkiConnect" for issue in result.issues)
        ffmpeg_ok = all(issue.component != "ffmpeg" for issue in result.issues)
        self.status_bar.set_system_status(ankiconnect_ok, ffmpeg_ok)
        # The timestamp is taken here, on the GUI thread, when the answer
        # actually reached the user — not inside the worker, where it would age
        # by however long the result queued.
        self._publish_health(self._health_report.with_validation(result, datetime.now()))

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
            self.clear_screen_issue()
        elif not silent:
            # A wall of "- component: message" lines was the whole modal. The
            # sentence says what happened; the component list is the diagnostic
            # and goes behind Details (D24).
            issues_text = "\n".join([f"- {issue.component}: {issue.message}" for issue in result.issues])
            self.show_screen_issue(
                ScreenIssue(
                    summary=self.tr("Some system checks need attention."),
                    details=issues_text,
                    action_id="settings.open",
                    action_text=self.tr("Open Settings"),
                ),
                action=self._open_settings,
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
        """Count one finished item into the session totals. Nothing more.

        This used to execute a modal ``ResultsDialog`` per result, so a
        twenty-item queue ended in twenty dialogs, each blocking the next until
        it was clicked away (D20). The run's outcome now lands on the screen
        that started it, as an inline receipt; the details surface opens from
        that receipt's **View details**, through :meth:`_on_run_details`.

        Args:
            result: One item's processing result.
        """
        self.status_bar.increment_cards_created(result.cards_created)

    def _on_run_details(self, result: ProcessingResult) -> None:
        """Open the full details of a finished run, because the user asked.

        Args:
            result: The whole run, aggregated into one result by its receipt.
        """

        from anki_miner.gui.widgets.inline_receipt import InlineReceipt

        originating_receipt = InlineReceipt.current_details_origin()
        originating_run_receipt = originating_receipt.receipt if originating_receipt is not None else None
        # known_words rows have no run identity. The modal dialog blocks new
        # starts; this gate covers mining tasks that were already running.
        # Deck Builder owns its workers directly and does not publish to the
        # task registry, so every current or retained QThread is authoritative.
        deck_builder_index = self._main_tab_index("deckbuilder")
        deck_builder_workers = []
        if deck_builder_index >= 0:
            deck_builder_tab = self.tabs.widget(deck_builder_index)
            deck_builder_workers.append(getattr(deck_builder_tab, "worker_thread", None))
            deck_builder_workers.extend(worker for worker, _processor in getattr(deck_builder_tab, "_leaked_runs", ()))
        mining_task_active = any(still_running(worker) for worker in deck_builder_workers) or any(
            snapshot.owner.main_tab in {"video", "deckbuilder", "audiobook", "reading"}
            for snapshot in self.task_registry.running()
        )

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
        def on_undo_committed(deleted: int) -> None:
            self.status_bar.increment_cards_created(-deleted)
            if (
                originating_receipt is not None
                and widget_alive(originating_receipt)
                and originating_receipt.receipt is originating_run_receipt
            ):
                originating_receipt.clear()

        dialog = ResultsDialog(
            result,
            self,
            undo_callback=None if mining_task_active else undo_callback,
            on_undo_committed=on_undo_committed,
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

    def is_shutting_down(self) -> bool:
        """True once ``closeEvent`` has committed to quitting the application.

        ``QWidget.close()`` reports ``False`` for a close the shutdown policy
        *deferred* as well as for one that was refused: the deferred arm ignores
        the event so Qt keeps the window and its still-running QThreads alive,
        then quits from a poll once the last laggard exits. A caller that reads
        that ``False`` as "the window is staying" undoes work that should still
        happen — the restart-to-apply path (D39b) cancelled its own relaunch
        that way whenever a worker outlived the join grace.
        """
        return self._close_committed

    def closeEvent(self, event) -> None:
        """Handle window close event.

        Delegates the shutdown join policy to the background-task controller:
        every owned and tab-owned worker is joined with a bounded grace, and
        workers that outlive it defer the close (controller hides the window,
        polls, then saves + quits) instead of being abandoned to Qt teardown.

        Args:
            event: Close event
        """
        # Where the user was, captured BEFORE anything can move or hide the
        # window: background_tasks.shutdown below may hand off to the deferred
        # close path, which hides the window and polls — and a hidden window's
        # geometry is not what the user left behind. One-shot, so a second close
        # attempt during that poll cannot overwrite the good value (D7).
        self._save_session_state()

        # Past this point the application is going, on both arms below: the
        # immediate one accepts the event, and the deferred one ignores it only
        # to keep the running QThreads alive until the poll quits. Callers that
        # asked for the close need to be able to tell those two apart from a
        # genuine refusal — see is_shutting_down.
        self._close_committed = True

        # Claim the one-time optional-boot slot before anything is joined. A
        # first-run wizard can still be open when the app is asked to quit, and
        # its exit path would otherwise start a validation, an update check and
        # a migration into a window that is already shutting down.
        self._post_setup_boot_started = True

        # System Health is a top-level window of its own, so Qt counts it when
        # deciding whether the last window has closed: left open, it keeps the
        # application alive after the main window is gone, showing readiness
        # facts for an app that is no longer running.
        if self._system_health_window is not None:
            self._system_health_window.close()

        # The monitor declines WA_QuitOnClose, so it cannot hold the application
        # open — but a window reporting on a run that is being torn down should
        # not be left on screen while the workers are joined.
        if self._mini_job_monitor is not None:
            self._mini_job_monitor.close()

        # File pickers are non-blocking now (gui/utils/file_dialogs), so one can
        # still be on screen here. It declines WA_QuitOnClose like the monitor
        # above, but its continuation would land in an application whose panels
        # and workers are about to be torn down — cancel outright, callback and
        # all.
        file_dialogs.cancel_all_pickers()

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
        """Open System Health (D26).

        The status control used to silently re-run validation, which answered a
        question nobody asked: the two badges beside it already say whether
        AnkiConnect and ffmpeg are up, and nothing showed what else was checked
        or what to do about it. It now opens the screen that does.
        """
        self.open_system_health()

    def open_system_health(self) -> None:
        """Show the permanent readiness screen, building it on first use.

        One parented, modeless instance for the window's lifetime. Closing it
        hides it — it owns no worker, so there is nothing to cancel and nothing
        to rebuild on the way back in.
        """
        window = self._system_health_window
        if window is None:
            window = SystemHealthWindow(self)
            window.recheck_requested.connect(self._run_validation)
            window.export_requested.connect(self._export_diagnostics)
            window.fix_requested.connect(self.reveal_setting)
            window.set_export_enabled(not self._diagnostics_export_running)
            self._system_health_window = window
            window.show_health(self._health_report)
        window.show()
        window.raise_()
        window.activateWindow()

    def open_mini_job_monitor(self) -> None:
        """Show the floating job monitor, building it on first use (D53).

        One parented, modeless instance for the window's lifetime. It opens on
        the run the status strip is already naming, so the two start out saying
        the same thing -- but only on the first build: reopening must not throw
        away the job the user went in and picked. Closing it hides it; it owns
        no worker, so there is nothing to cancel on the way out and nothing to
        rebuild on the way back in.
        """
        monitor = self._mini_job_monitor
        if monitor is None:
            monitor = MiniJobMonitor(self.task_registry, self)
            monitor.show_main_window_requested.connect(self.reveal_main_window)
            self._mini_job_monitor = monitor
            displayed = self.status_bar.displayed_run
            if displayed is not None:
                monitor.watch(*displayed)
        monitor.show()
        monitor.raise_()
        monitor.activateWindow()

    def reveal_main_window(self) -> None:
        """Bring the application back to the front, un-minimising it if needed.

        ``showNormal`` rather than ``show`` because the usual reason to press
        the monitor's button is that the main window is minimised, and ``show``
        on a minimised window leaves it minimised.
        """
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _publish_health(self, report: HealthReport) -> None:
        """Record the latest readiness facts and repaint the screen if it is up."""
        self._health_report = report
        self._log_health_snapshot(report)
        if self._system_health_window is not None:
            self._system_health_window.show_health(report)

    def _log_health_snapshot(self, report: HealthReport) -> None:
        """Log complete health state once, then only after a state change."""
        validation_checks = [report.get(key) for key in HEALTH_KEYS if key != "app.updates"]
        if not any(check.state != HEALTH_UNKNOWN for check in validation_checks):
            return

        states = {key: report.get(key).state for key in HEALTH_KEYS}
        if states == self._last_logged_health_states:
            return
        self._last_logged_health_states = states

        rows = []
        for key in HEALTH_KEYS:
            check = report.get(key)
            rows.append((check.key, check.state, check.detail, check.checked_at))
        for line in format_health_lines(rows):
            logger.info("health %s", line)

    def _start_environment_snapshot(self) -> None:
        """Dispatch the blocking environment probes to a worker."""
        # platformName() is GUI-thread only, so it is read HERE and passed in —
        # collect_environment itself stays Qt-free and worker-safe.
        platform_name = QGuiApplication.platformName()
        run_off_thread(
            self,
            lambda: collect_environment(self.config, platform_name=platform_name),
            self._on_environment_snapshot,
        )

    def _on_environment_snapshot(self, result: object) -> None:
        """Log one stable line per collected environment field."""
        if not isinstance(result, EnvironmentSnapshot):
            return
        for line in format_environment_lines(result):
            logger.info("env %s", line)

    def reveal_setting(self, stable_id: str) -> None:
        """Open Settings on the exact control ``stable_id`` addresses (D11).

        The anchor-precise sibling of :meth:`reveal_capability`, and the target
        of every System Health **Fix** button. Resolving an id to a page, a
        scroll position, focus and a highlight stays in ``SettingsTab``; an
        unknown id is ignored there, so a stale deep link cannot crash the UI.
        """
        idx = self._settings_tab_index()
        if idx < 0:
            return
        self.tabs.setCurrentIndex(idx)
        jump = getattr(self.tabs.widget(idx), "jump_to_setting", None)
        if callable(jump):
            jump(stable_id)

    def _run_validation(self) -> None:
        """Run system validation in background thread."""
        # The controller declines when a validation run is already in flight.
        # The current (config-bound) service is passed per call so the rebuild
        # in _build_config_bound_services reaches the next run.
        if not self.background_tasks.start_validation(self.validation_service):
            self.status_bar.set_operation(self.tr("Validation already running"), "info")
            return
        # Both the badges and every health row go back to "not known yet". A
        # probe in flight is not a failure, and the previous sweep's answers are
        # no longer the answers to the question now being asked.
        self.status_bar.set_system_status_checking()
        self._publish_health(self._health_report.checking())
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

        self.status_bar.set_operation(self.tr("System check failed. Try again."), "error")
        # The sweep failed, so nothing was learnt: every row goes back to
        # unknown rather than inheriting the failure of the run that carried it.
        self._publish_health(self._health_report.with_validation_error(error_message))
        if not silent:
            self.show_screen_issue(
                ScreenIssue(
                    summary=self.tr("System check failed. Try again."),
                    details=error_message,
                    action_id="validation.retry",
                    action_text=self.tr("Retry"),
                ),
                action=self._run_validation,
            )

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
                no update. Checker failures arrive as exceptions.
        """
        from anki_miner.gui.widgets.update_banner import UpdateBanner
        from anki_miner.services.update_checker import UpdateInfo

        # System Health's Updates row is written on every outcome, including the
        # "nothing newer" one that returns below — a row that only ever changed
        # when an update existed would sit at "not checked yet" forever on an
        # up-to-date install.
        if isinstance(info, UpdateInfo):
            state = HEALTH_WARN
            detail = tr_format(self.tr("Version %1 is available."), info.version)
        elif info is None:
            state = HEALTH_OK
            detail = tr_format(self.tr("Running %1. No newer release was reported."), __version__)
        elif isinstance(info, BaseException):
            state = HEALTH_UNKNOWN
            detail = self.tr("The update check failed; try again later.")
        else:
            return
        self._publish_health(
            self._health_report.with_update_check(state=state, detail=detail, checked_at=datetime.now())
        )

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
            # After the header and the issue banner: a release announcement
            # never outranks a system problem.
            self.central_layout.insertWidget(2, banner)
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
