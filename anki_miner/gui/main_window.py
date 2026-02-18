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

        # Auto-check system status on startup (silently, no popup)
        self._validation_silent = True
        self._run_validation()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        self.setWindowTitle("Anki Miner - Japanese Vocabulary Mining Tool")
        self.setMinimumSize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)
        self.resize(WINDOW_DEFAULT_WIDTH, WINDOW_DEFAULT_HEIGHT)

        # Create central widget with layout
        central_widget = QWidget()
        central_layout = QVBoxLayout()
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)

        # Add header
        self.header = HeaderWidget()
        self.header.theme_changed.connect(self._on_theme_changed)
        central_layout.addWidget(self.header)

        # Create tab widget
        self.tabs = QTabWidget()
        central_layout.addWidget(self.tabs)

        central_widget.setLayout(central_layout)
        self.setCentralWidget(central_widget)

        # Enhanced status bar
        self.status_bar = StatusBarWidget()
        self.status_bar.system_status_clicked.connect(self._on_system_status_clicked)
        self.setStatusBar(self.status_bar)

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
            "Navigate between Episode Mining, Batch Processing, and Settings"
        )

        self.header.setAccessibleName("Application Header")
        self.header.setAccessibleDescription("Application title and theme selector")

        self.status_bar.setAccessibleName("Status Bar")
        self.status_bar.setAccessibleDescription(
            "Shows current operation, statistics, and system status"
        )

        # Set tab order: header -> tabs -> status bar
        self.setTabOrder(self.header, self.tabs)

    def _setup_shortcuts(self) -> None:
        """Set up global keyboard shortcuts."""
        # Tab switching shortcuts (Ctrl+1/2/3)
        for i in range(1, 4):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{i}"), self)
            shortcut.activated.connect(lambda idx=i - 1: self._switch_to_tab(idx))

        # Theme toggle (Ctrl+T)
        theme_shortcut = QShortcut(QKeySequence("Ctrl+T"), self)
        theme_shortcut.activated.connect(self._cycle_theme)

        # Settings shortcut (Ctrl+,)
        settings_shortcut = QShortcut(QKeySequence("Ctrl+,"), self)
        settings_shortcut.activated.connect(self._open_settings)

        # Help/About (F1)
        help_shortcut = QShortcut(QKeySequence("F1"), self)
        help_shortcut.activated.connect(self._show_about)

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
            <li>Three beautiful themes: Light, Dark, Sakura</li>
        </ul>
        <br>
        <p><b>Keyboard Shortcuts:</b></p>
        <ul>
            <li><b>Ctrl+1/2/3:</b> Switch tabs</li>
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

        # Show results dialog
        dialog = ResultsDialog(result, self)
        dialog.exec()

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
