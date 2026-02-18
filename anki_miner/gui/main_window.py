"""Main window for Anki Miner GUI."""

from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QApplication, QMainWindow, QMessageBox, QTabWidget, QVBoxLayout, QWidget

from anki_miner import __version__
from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.constants import (
    TAB_SETTINGS,
    THEME_ORDER,
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
from anki_miner.services import ValidationService


class MainWindow(QMainWindow):
    """Main application window for Anki Miner.

    This window provides a tabbed interface for:
    - Single episode mining
    - Batch folder processing
    - Settings/configuration
    """

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
        self._validation_silent = False

        # Connect presenter signals
        self._connect_presenter_signals()

        # Set up UI
        self._setup_ui()

        # Track update worker
        self.update_worker = None

        # Auto-check system status on startup (silently, no popup)
        self._validation_silent = True
        self._run_validation()

        # Auto-check for updates on startup
        if self.config.check_for_updates:
            self._check_for_updates()

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
        self.setAccessibleDescription(
            "Japanese vocabulary mining tool for creating Anki flashcards from anime"
        )

        # Set accessible names for main components
        self.tabs.setAccessibleName("Main Tabs")
        self.tabs.setAccessibleDescription(
            "Navigate between Episode Mining, Batch Processing, Analytics, and Settings"
        )

        self.header.setAccessibleName("Application Header")
        self.header.setAccessibleDescription("Application title and theme selector")

        self.status_bar.setAccessibleName("Status Bar")
        self.status_bar.setAccessibleDescription(
            "Shows current operation, statistics, and system status"
        )

        # Set tab order: header -> tabs -> status bar
        self.setTabOrder(self.header, self.tabs)

    def _setup_menu_bar(self) -> None:
        """Set up the application menu bar."""
        menu_bar = self.menuBar()

        # Help menu
        help_menu = menu_bar.addMenu("&Help")

        about_action = help_menu.addAction("About Anki Miner")
        about_action.setShortcut(QKeySequence("F1"))
        about_action.triggered.connect(self._show_about)

        help_menu.addSeparator()

        report_action = help_menu.addAction("Report an Issue")
        report_action.triggered.connect(self._report_issue)

        check_updates_action = help_menu.addAction("Check for Updates")
        check_updates_action.triggered.connect(self._check_for_updates)

    def _setup_shortcuts(self) -> None:
        """Set up global keyboard shortcuts."""
        # Tab switching shortcuts (Ctrl+1/2/3/4)
        for i in range(1, 5):
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
        """Cycle through available themes: Light → Dark → Sakura → Light."""
        current_theme = self.header.theme_selector.currentText().lower()

        try:
            current_index = THEME_ORDER.index(current_theme)
            next_index = (current_index + 1) % len(THEME_ORDER)
        except ValueError:
            next_index = 0  # Default to first theme if current not found

        self.header.theme_selector.setCurrentIndex(next_index)

    def _open_settings(self) -> None:
        """Open the Settings tab."""
        self.tabs.setCurrentIndex(TAB_SETTINGS)

    def _report_issue(self) -> None:
        """Open the GitHub issues page in the default browser."""
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl("https://github.com/0xzerolight/anki_miner/issues"))

    def _show_about(self) -> None:
        """Show the About dialog."""
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
            <li>Single episode mining with preview</li>
            <li>Batch processing for entire series</li>
            <li>Automatic media extraction (screenshots & audio)</li>
            <li>Dictionary definitions from JMDict</li>
            <li>Frequency filtering to focus on common words</li>
            <li>Mining analytics and progress tracking</li>
            <li>Three beautiful themes: Light, Dark, Sakura</li>
        </ul>
        <br>
        <p><b>Keyboard Shortcuts:</b></p>
        <ul>
            <li><b>Ctrl+1/2/3/4:</b> Switch tabs</li>
            <li><b>Ctrl+T:</b> Cycle themes</li>
            <li><b>Ctrl+,:</b> Open Settings</li>
            <li><b>Ctrl+Shift+V:</b> Run system validation</li>
            <li><b>F1:</b> Show this help dialog</li>
        </ul>
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
            issues_text = "\n".join(
                [f"- {issue.component}: {issue.message}" for issue in result.issues]
            )
            QMessageBox.warning(
                self, "Validation Issues", f"System validation found issues:\n\n{issues_text}"
            )

    def _on_processing_result(self, result: ProcessingResult) -> None:
        """Handle processing result from presenter.

        Args:
            result: Processing result to display
        """
        # Update session statistics
        self.status_bar.increment_cards_created(result.cards_created)

        # Create undo callback
        def undo_callback(note_ids: list[int]) -> int:
            from anki_miner.services.anki_service import AnkiService

            service = AnkiService(self.config)
            deleted = service.delete_notes(note_ids)
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
        import logging

        from anki_miner.services.history_service import HistoryService

        try:
            service = HistoryService(self.config.history_db_path)
            service.initialize()
            from pathlib import Path

            service.record_session(
                video_file=Path(result.video_file) if result.video_file else Path("unknown"),
                subtitle_file=(
                    Path(result.subtitle_file) if result.subtitle_file else Path("unknown")
                ),
                result=result,
                card_ids=result.card_ids,
            )
        except Exception:
            logging.getLogger(__name__).debug("Failed to record history", exc_info=True)

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

        # Cancel and wait for any processing workers in tabs
        from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab
        from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab

        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            if isinstance(tab, SingleEpisodeTab | BatchProcessingTab):
                worker = getattr(tab, "worker_thread", None)
                if worker and worker.isRunning():
                    worker.cancel()
                    worker.wait(2000)

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
            self.status_bar.set_operation("Validation already in progress", "info")
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

    def _on_update_check_result(
        self, update_available: bool, latest_version: str, release_url: str
    ) -> None:
        """Handle update check result.

        Args:
            update_available: Whether a newer version exists
            latest_version: The latest version string
            release_url: URL to the release page
        """
        if update_available:
            from anki_miner.gui.widgets.update_banner import UpdateBanner

            # Remove existing banner if present
            for i in range(self.central_layout.count()):
                item = self.central_layout.itemAt(i)
                if item and isinstance(item.widget(), UpdateBanner):
                    item.widget().deleteLater()
                    break

            banner = UpdateBanner(latest_version, release_url, self)
            # Insert banner after header (index 1)
            self.central_layout.insertWidget(1, banner)

    def _on_theme_changed(self, theme_name: str) -> None:
        """Handle theme change from header widget.

        Args:
            theme_name: Name of the new theme
        """
        # Get and apply new stylesheet
        stylesheet = Theme.get_stylesheet(theme_name)  # type: ignore[arg-type]
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.setStyleSheet(stylesheet)

        # Update header to reflect current theme
        self.header.update_theme_selector()
