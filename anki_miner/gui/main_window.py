"""Main window for Anki Miner GUI."""

import logging
from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
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
    TAB_SETTINGS,
    WINDOW_DEFAULT_HEIGHT,
    WINDOW_DEFAULT_WIDTH,
    WINDOW_MIN_HEIGHT,
    WINDOW_MIN_WIDTH,
)
from anki_miner.gui.presenters import GUIPresenter
from anki_miner.gui.resources.styles.theme import Theme
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.widgets.dialogs.results_dialog import ResultsDialog
from anki_miner.gui.widgets.dialogs.word_preview_dialog import WordPreviewDialog
from anki_miner.gui.widgets.header_widget import HeaderWidget
from anki_miner.gui.widgets.status_bar_widget import StatusBarWidget
from anki_miner.gui.workers.validation_worker import ValidationWorkerThread
from anki_miner.models import ProcessingResult, ValidationResult
from anki_miner.services import ShortcutService, ValidationService
from anki_miner.services.anki_service import AnkiService

logger = logging.getLogger(__name__)

_ABOUT_FEATURES: list[str] = [
    "Single episode mining with preview",
    "Batch processing for entire series",
    "YouTube URL mining (yt-dlp powered)",
    "Automatic media extraction (screenshots & audio)",
    "Dictionary definitions from JMdict",
    "Frequency filtering to focus on common words",
    "Mining analytics and progress tracking",
    "Customizable theme system with user-installable themes",
]

_ABOUT_SHORTCUTS: list[tuple[str, str]] = [
    ("Ctrl+1..5", "Switch tabs"),
    ("Ctrl+T", "Cycle favorite themes"),
    ("Ctrl+,", "Open Settings"),
    ("Ctrl+Shift+V", "Run system validation"),
    ("F1", "Show this help dialog"),
]


def _needs_jmdict_migration(xml_path: Path, dicts_root: Path, chain: tuple | None = None) -> bool:
    """Return True iff we should auto-trigger the JMdict → SQLite migration.

    Triggers only when:
      * legacy XML is on disk,
      * no SQLite index exists yet, AND
      * the user's chain has jmdict-english enabled (no point parsing 60MB
        XML for someone who explicitly disabled offline lookups).

    The chain check is skipped when chain is None to keep backward-compatible
    behaviour with the unit tests that just probe file presence.
    """
    if not xml_path.exists():
        return False
    if (dicts_root / "jmdict-english" / "index.sqlite").exists():
        return False
    if chain is None:
        return True
    return any(
        getattr(e, "kind", None) == "indexed"
        and getattr(e, "dict_id", None) == "jmdict-english"
        and getattr(e, "enabled", False)
        for e in chain
    )


class MainWindow(QMainWindow):
    """Main application window for Anki Miner.

    This window provides a tabbed interface for:
    - Episode Mining (single video + subtitle pair)
    - Batch Mining (folder of paired files)
    - YouTube (URL probe + fetch + mine)
    - Analytics (mining statistics dashboard)
    - Settings (configuration)

    Signals:
        config_refreshed: emitted when a non-Settings code path mutates
            self.config (e.g. background JMdict migration finishes). Tabs
            that cache services should reconnect this to their update_config
            so they pick up the new state without waiting for the user to
            edit Settings.
    """

    config_refreshed = pyqtSignal(object)  # AnkiMinerConfig

    def __init__(self):
        """Initialize the main window."""
        super().__init__()

        # Load configuration
        self.config = GUIConfigManager.load_config()

        # Create presenter for validation signals
        self.presenter = GUIPresenter(self)

        # Create validation service
        self.validation_service = ValidationService(self.config)
        self.validation_worker = None

        # AnkiService instance shared across undo callbacks (avoids constructing
        # a fresh instance per result — reuses the same config-bound service).
        self._anki_service = AnkiService(self.config)
        self._validation_silent = False

        # Connect presenter signals
        self._connect_presenter_signals()

        # Set up UI
        self._setup_ui()

        # Track update worker
        self.update_worker = None

        # Singleton update banner — None until the first check yields a result.
        # Reused across update checks via UpdateBanner.update_info() to avoid
        # racing in-flight Qt callbacks against a destroyed C++ object.
        from anki_miner.gui.widgets.update_banner import UpdateBanner

        self._update_banner: UpdateBanner | None = None

        # Auto-check system status on startup (silently, no popup)
        self._validation_silent = True
        self._run_validation()

        # Auto-check for updates on startup
        if self.config.check_for_updates:
            self._check_for_updates()

        # One-time JMdict XML → SQLite migration (background)
        self._jmdict_migration_worker = None
        self._maybe_migrate_jmdict()

        # Best-effort cache prewarm worker, scheduled by ``app.main()`` after
        # the first paint. Held here so the QThread isn't GC'd mid-run and so
        # ``closeEvent`` can wait for it; cleared once it finishes.
        self._prewarm_worker = None

        # Post-update confirmation: if last_known_version differs from the
        # currently running __version__, show a one-shot info dialog. Save the
        # new version BEFORE showing the dialog so a crash mid-dialog doesn't
        # cause it to re-fire on the next launch. First launch (empty string)
        # writes silently.
        previous = self.config.last_known_version
        if previous != __version__:
            self.update_config(replace(self.config, last_known_version=__version__))
            if previous:
                QMessageBox.information(
                    self,
                    "Anki Miner updated",
                    (
                        f"Updated to v{__version__}.<br><br>"
                        "See what's new: "
                        '<a href="https://github.com/0xzerolight/anki_miner/releases/latest">'
                        "release notes</a>"
                    ),
                )

        # First-run desktop shortcut (deferred so the window paints first)
        if not self.config.first_run_shortcut_done:
            QTimer.singleShot(0, self._maybe_create_shortcut_on_first_run)

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        self.setWindowTitle("Anki Miner - Japanese Vocabulary Mining Tool")
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
        self.central_layout.addWidget(self.header)

        # Create tab widget
        self.tabs = QTabWidget()
        self.central_layout.addWidget(self.tabs)

        central_widget.setLayout(self.central_layout)
        self.setCentralWidget(central_widget)

        # Enhanced status bar
        self.status_bar = StatusBarWidget()
        self.status_bar.system_status_clicked.connect(self._on_system_status_clicked)
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
        self.setAccessibleName("Anki Miner Main Window")
        self.setAccessibleDescription("Japanese vocabulary mining tool for creating Anki flashcards from anime")

        # Set accessible names for main components
        self.tabs.setAccessibleName("Main Tabs")
        self.tabs.setAccessibleDescription(
            "Navigate between Episode Mining, Batch Mining, YouTube, Analytics, and Settings"
        )

        self.header.setAccessibleName("Application Header")
        self.header.setAccessibleDescription("Application title and theme selector")

        self.status_bar.setAccessibleName("Status Bar")
        self.status_bar.setAccessibleDescription("Shows current operation, statistics, and system status")

        # Set tab order: header -> tabs -> status bar
        self.setTabOrder(self.header, self.tabs)

    def _setup_menu_bar(self) -> None:
        """Set up the application menu bar."""
        menu_bar = self.menuBar()
        assert menu_bar is not None

        # Tools menu
        tools_menu = menu_bar.addMenu("&Tools")
        assert tools_menu is not None
        shortcut_action = tools_menu.addAction("Create Desktop Shortcut...")
        assert shortcut_action is not None
        shortcut_action.triggered.connect(self._create_desktop_shortcut)

        # Help menu
        help_menu = menu_bar.addMenu("&Help")
        assert help_menu is not None

        about_action = help_menu.addAction("About Anki Miner")
        assert about_action is not None
        about_action.setShortcut(QKeySequence("F1"))
        about_action.triggered.connect(self._show_about)

        help_menu.addSeparator()

        check_updates_action = help_menu.addAction("Check for Updates")
        assert check_updates_action is not None
        check_updates_action.triggered.connect(self._check_for_updates)

        # Top-right corner of the menu bar holds a small button bar. A QMenuBar
        # allows only one corner widget per corner, so both buttons live inside
        # a container QWidget laid out horizontally.
        corner_widget = QWidget(menu_bar)
        corner_layout = QHBoxLayout(corner_widget)
        corner_layout.setContentsMargins(0, 0, 0, 0)
        corner_layout.setSpacing(0)

        # "Report a Bug / Suggest a Feature" button (moved out of the Help menu).
        report_button = QToolButton(corner_widget)
        report_button.setObjectName("report_issue_button")
        report_button.setText("Report a Bug / Suggest a Feature")
        report_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        report_button.setAutoRaise(True)
        report_button.setToolTip("Report a bug or suggest a feature on GitHub")
        report_button.clicked.connect(self._report_issue)
        corner_layout.addWidget(report_button)

        # "Star on GitHub" button.
        star_button = QToolButton(corner_widget)
        star_button.setObjectName("github_star_button")
        star_button.setText("⭐ Star - help the project")
        star_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        star_button.setAutoRaise(True)
        star_button.setToolTip("Star the project on GitHub")
        star_button.clicked.connect(self._open_github_repo)
        corner_layout.addWidget(star_button)

        menu_bar.setCornerWidget(corner_widget, Qt.Corner.TopRightCorner)

    def _setup_shortcuts(self) -> None:
        """Set up global keyboard shortcuts."""
        # Tab switching shortcuts (Ctrl+1..Ctrl+5)
        for i in range(1, 6):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{i}"), self)
            shortcut.activated.connect(lambda idx=i - 1: self._switch_to_tab(idx))

        # Theme toggle (Ctrl+T)
        theme_shortcut = QShortcut(QKeySequence("Ctrl+T"), self)
        theme_shortcut.activated.connect(self._cycle_theme)

        # Settings shortcut (Ctrl+,)
        settings_shortcut = QShortcut(QKeySequence("Ctrl+,"), self)
        settings_shortcut.activated.connect(self._open_settings)

        # System validation (Ctrl+Shift+V)
        validation_shortcut = QShortcut(QKeySequence("Ctrl+Shift+V"), self)
        validation_shortcut.activated.connect(self._run_validation)

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

    def _open_settings(self) -> None:
        """Open the Settings tab."""
        self.tabs.setCurrentIndex(TAB_SETTINGS)

    def _open_theme_settings(self) -> None:
        """Switch to Settings → Themes (triggered by 'All themes…' sentinel)."""
        self.tabs.setCurrentIndex(TAB_SETTINGS)
        # The Settings tab widget was registered by app.py at TAB_SETTINGS;
        # call through to its convenience method to land on the right sub-tab.
        settings_widget = self.tabs.widget(TAB_SETTINGS)
        open_subtab = getattr(settings_widget, "open_themes_subtab", None)
        if callable(open_subtab):
            open_subtab()

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

    def _create_desktop_shortcut(self) -> None:
        """Create a desktop shortcut via ShortcutService and report the result."""
        result = ShortcutService.create_shortcut()
        body = "\n".join(result.messages) if result.messages else ""
        if result.success:
            QMessageBox.information(self, "Desktop Shortcut", body or "Shortcut created.")
        else:
            QMessageBox.warning(
                self,
                "Desktop Shortcut",
                result.error or "Failed to create desktop shortcut.",
            )

    def _maybe_create_shortcut_on_first_run(self) -> None:
        """Auto-create a desktop shortcut on first launch; persist the flag."""
        if not ShortcutService.shortcut_exists():
            ShortcutService.create_shortcut()
        self.update_config(replace(self.config, first_run_shortcut_done=True))

    def _show_about(self) -> None:
        """Show the About dialog."""
        features_html = "".join(f"            <li>{f}</li>\n" for f in _ABOUT_FEATURES)
        shortcuts_html = "".join(f"            <li><b>{key}:</b> {desc}</li>\n" for key, desc in _ABOUT_SHORTCUTS)
        about_text = f"""
        <h2>Anki Miner</h2>
        <p><b>Version:</b> {__version__}</p>
        <p><b>Description:</b> Turn Immersion Into Vocabulary</p>
        <br>
        <p>Anki Miner helps you create Japanese vocabulary flashcards from video content.</p>
        <p>Extract words with screenshots, audio, and definitions directly to Anki.</p>
        <br>
        <p><b>Features:</b></p>
        <ul>
{features_html}        </ul>
        <br>
        <p><b>Keyboard Shortcuts:</b></p>
        <ul>
{shortcuts_html}        </ul>
        <br>
        <p><b>Requirements:</b></p>
        <ul>
            <li>Anki with AnkiConnect add-on</li>
            <li>ffmpeg for media extraction</li>
            <li>MeCab for Japanese tokenization</li>
        </ul>
        <br>
        <p>Created for Japanese learners</p>
        """
        QMessageBox.about(self, "About Anki Miner", about_text)

    def _connect_presenter_signals(self) -> None:
        """Connect presenter signals to UI update slots."""
        self.presenter.info_signal.connect(self._on_info_message)
        self.presenter.success_signal.connect(self._on_success_message)
        self.presenter.warning_signal.connect(self._on_warning_message)
        self.presenter.error_signal.connect(self._on_error_message)
        self.presenter.validation_result_signal.connect(self._on_validation_result)
        self.presenter.processing_result_signal.connect(self._on_processing_result)
        self.presenter.word_preview_signal.connect(self._on_word_preview)

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

        # Update system status indicators
        ankiconnect_ok = all(issue.component != "AnkiConnect" for issue in result.issues)
        ffmpeg_ok = all(issue.component != "ffmpeg" for issue in result.issues)
        self.status_bar.set_system_status(ankiconnect_ok, ffmpeg_ok)

        if result.all_passed:
            self.status_bar.set_operation("System validation passed", "success")
        elif not silent:
            # Show validation issues (skip popup during startup auto-check)
            issues_text = "\n".join([f"- {issue.component}: {issue.message}" for issue in result.issues])
            QMessageBox.warning(self, "Validation Issues", f"System validation found issues:\n\n{issues_text}")

    def _on_processing_result(self, result: ProcessingResult) -> None:
        """Handle processing result from presenter.

        Args:
            result: Processing result to display
        """
        # Update session statistics
        self.status_bar.increment_cards_created(result.cards_created)

        # Create undo callback
        def undo_callback(note_ids: list[int]) -> int:
            deleted = self._anki_service.delete_notes(note_ids)
            self.status_bar.increment_cards_created(-deleted)
            return deleted

        # Show results dialog with undo support
        dialog = ResultsDialog(result, self, undo_callback=undo_callback)
        dialog.exec()

        # Record to history after dialog closes (skip if user undid the cards)
        if self.config.enable_history and result.cards_created > 0 and not dialog.undo_completed:
            self._record_history(result)

    def _record_history(self, result: ProcessingResult) -> None:
        """Record processing result to history database.

        Args:
            result: Processing result to record
        """
        from anki_miner.services.history_service import HistoryService

        try:
            service = HistoryService(self.config.history_db_path)
            service.initialize()
            service.record_session(
                video_file=Path(result.video_file) if result.video_file else Path("unknown"),
                subtitle_file=(Path(result.subtitle_file) if result.subtitle_file else Path("unknown")),
                result=result,
                card_ids=result.card_ids,
            )
        except Exception:
            logger.debug("Failed to record history", exc_info=True)

    def _on_word_preview(self, words: list) -> None:
        """Handle word preview from presenter.

        Args:
            words: List of TokenizedWord objects
        """
        dialog = WordPreviewDialog(words, self.config, self)
        dialog.exec()

    def get_config(self) -> AnkiMinerConfig:
        """Get current configuration.

        Returns:
            Current configuration
        """
        return self.config

    def update_config(self, config: AnkiMinerConfig) -> None:
        """Update configuration and save to disk.

        Args:
            config: New configuration
        """
        self.config = config
        GUIConfigManager.save_config(config)

    def release_dictionary_resources(self) -> bool:
        """Ask every tab to release cached dictionary handles.

        Used by the Settings → Remove dictionary flow to drop SQLite handles
        before ``rmtree`` (Issue #30, Win11 file-lock). Returns ``False`` if
        any tab refused — typically because a mining run is in flight — so
        the caller can surface a clear message instead of silently failing.
        """
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            release = getattr(tab, "release_dictionary_resources", None)
            if callable(release) and not release():
                return False
        return True

    def closeEvent(self, event) -> None:
        """Handle window close event.

        Args:
            event: Close event
        """
        # Cancel and wait for validation worker if running
        if self.validation_worker and self.validation_worker.isRunning():
            self.validation_worker.cancel()
            self.validation_worker.wait(2000)

        # Cancel and wait for update worker if running
        if self.update_worker and self.update_worker.isRunning():
            self.update_worker.cancel()
            self.update_worker.wait(2000)

        # Cancel and wait for JMdict migration worker if running
        if self._jmdict_migration_worker and self._jmdict_migration_worker.isRunning():
            self._jmdict_migration_worker.cancel()
            self._jmdict_migration_worker.wait(2000)

        # Wait for the best-effort prewarm worker if still running. It has no
        # cancel hook (it's a short, uninterruptible cache warm) so we just
        # join it briefly to avoid Qt destroying a running QThread.
        if self._prewarm_worker is not None and self._prewarm_worker.isRunning():
            self._prewarm_worker.wait(2000)

        # Cancel and wait for any processing workers in tabs
        from anki_miner.gui.widgets.youtube_tab import YouTubeTab

        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            # All mining tabs expose their worker on `worker_thread`.
            # DeckBuilderWorker.cancel() also opens its confirm gate, so a worker
            # blocked awaiting Build unblocks and exits.
            worker = getattr(tab, "worker_thread", None)
            if worker and worker.isRunning():
                worker.cancel()
                worker.wait(2000)
            # YouTube tab owns an additional probe worker; shutdown() tears
            # both threads down cleanly.
            if isinstance(tab, YouTubeTab) and hasattr(tab, "shutdown"):
                tab.shutdown()

        # Save configuration before closing
        GUIConfigManager.save_config(self.config)
        event.accept()

    def _on_system_status_clicked(self) -> None:
        """Handle system status indicator click."""
        # Trigger system validation
        self._run_validation()

    def _run_validation(self) -> None:
        """Run system validation in background thread."""
        # Don't start a new validation if one is already running
        if self.validation_worker is not None and self.validation_worker.isRunning():
            self.status_bar.set_operation("Validation already running", "info")
            return

        # Update status bar
        self.status_bar.set_operation("Running system validation...", "info")

        # Create and start validation worker
        self.validation_worker = ValidationWorkerThread(self.validation_service, self)
        self.validation_worker.result_ready.connect(self._on_validation_finished)
        self.validation_worker.error.connect(self._on_validation_error)
        self.validation_worker.start()

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

        self.status_bar.set_operation(f"Validation error: {error_message}", "error")
        if not silent:
            QMessageBox.critical(self, "Validation Error", error_message)

    def _maybe_migrate_jmdict(self) -> None:
        """One-time: migrate legacy JMdict XML into a SQLite index in the background."""
        from anki_miner.gui.workers.dictionary_import_worker import DictionaryImportWorker

        dicts_root = self.config.dicts_root
        if not _needs_jmdict_migration(self.config.jmdict_path, dicts_root, self.config.dictionary_chain):
            return

        self._jmdict_migration_worker = DictionaryImportWorker.for_jmdict(self.config.jmdict_path, dicts_root)
        self._jmdict_migration_worker.import_finished.connect(self._on_jmdict_migration_finished)
        self._jmdict_migration_worker.failed.connect(lambda err: logger.warning("JMdict migration failed: %s", err))
        logger.info("Starting one-time JMdict SQLite migration")
        self.status_bar.set_operation("Migrating JMdict to SQLite…", "info")
        self._jmdict_migration_worker.start()

    def _on_jmdict_migration_finished(self, dict_id: str, meta: dict) -> None:
        """Notify tabs that they need to rebuild any cached DefinitionService.

        We don't mutate config here — the chain entry is already correct (it
        was the trigger). We re-emit so YouTubeTab (and any future caching
        tab) rebuilds its processor and picks up the newly-available index.
        """
        logger.info("JMdict migration complete: %s (%s entries)", dict_id, meta.get("entry_count"))
        self.status_bar.set_operation(f"JMdict ready ({meta.get('entry_count', 0):,} entries)", "info")
        self.config_refreshed.emit(self.config)

    def _check_for_updates(self) -> None:
        """Check for application updates in background thread."""
        if self.update_worker and self.update_worker.isRunning():
            return

        from anki_miner import __version__
        from anki_miner.gui.workers.update_worker import UpdateWorkerThread
        from anki_miner.services.update_checker import UpdateChecker

        checker = UpdateChecker(__version__)
        self.update_worker = UpdateWorkerThread(checker, self)
        self.update_worker.result_ready.connect(self._on_update_check_result)
        self.update_worker.start()

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

        if self._update_banner is None:
            banner = UpdateBanner(info, self)
            banner.skip_requested.connect(self._on_skip_update_requested)
            # Insert banner after header (index 1).
            self.central_layout.insertWidget(1, banner)
            self._update_banner = banner
        else:
            self._update_banner.update_info(info)
            self._update_banner.setVisible(True)

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
