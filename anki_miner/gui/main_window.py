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

# Shutdown join policy knobs (see MainWindow._join_worker_for_close):
# grace period each cancellable worker gets to exit during closeEvent before
# the close is deferred, and the poll cadence while a deferred close waits
# for laggard threads to finish.
_CLOSE_JOIN_GRACE_MS = 2000
_CLOSE_POLL_INTERVAL_MS = 200


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
        config_refreshed: emitted whenever a non-Settings code path updates
            self.config — every ``update_config`` call except the Settings
            save path (which passes ``from_settings=True``), plus the
            background JMdict migration finishing. Tabs that cache services
            (and SettingsTab, so its panels don't go stale) reconnect this to
            their update_config to pick up the new state without waiting for
            the user to edit Settings.
    """

    config_refreshed = pyqtSignal(object)  # AnkiMinerConfig

    def __init__(self):
        """Initialize the main window."""
        super().__init__()

        # Load configuration
        self.config = GUIConfigManager.load_config()

        # Create presenter for validation signals
        self.presenter = GUIPresenter(self)

        # Config-bound services (validation + the AnkiService shared across undo
        # callbacks). Rebuilt on every config change via update_config — see
        # _build_config_bound_services — so an AnkiConnect URL/port edit reaches
        # the next Undo delete instead of the stale startup endpoint.
        self.validation_worker = None
        self._build_config_bound_services()
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

        # Deferred-close state: poll timer + workers that outlived the grace
        # join in closeEvent (see _join_worker_for_close for the policy).
        self._close_poll_timer: QTimer | None = None
        self._close_laggards: list = []

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

    def _settings_tab_index(self) -> int:
        """Locate the Settings tab by capability (self-healing against tab reorder)."""
        for i in range(self.tabs.count()):
            if hasattr(self.tabs.widget(i), "open_themes_subtab"):
                return i
        for i in range(self.tabs.count()):  # fallback by label
            if self.tabs.tabText(i) == "Settings":
                return i
        return -1

    def _open_settings(self) -> None:
        """Open the Settings tab."""
        idx = self._settings_tab_index()
        if idx >= 0:
            self.tabs.setCurrentIndex(idx)

    def _open_theme_settings(self) -> None:
        """Switch to Settings → Themes (triggered by 'All themes…' sentinel)."""
        idx = self._settings_tab_index()
        if idx < 0:
            return
        self.tabs.setCurrentIndex(idx)
        # Call through to the Settings tab's convenience method to land on the right sub-tab.
        settings_widget = self.tabs.widget(idx)
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

        # Drive the Settings → Anki connection badge so Test Connection and the
        # deck/note-type sync buttons (which all route through validation)
        # produce visible feedback (T-53). The badge otherwise sticks at
        # "Checking connection..." forever — set_connection_status had no
        # callers. Use the authoritative result.ankiconnect_ok flag.
        self._set_anki_connection_badge("connected" if result.ankiconnect_ok else "disconnected")

        if result.all_passed:
            self.status_bar.set_operation("System validation passed", "success")
        elif not silent:
            # Show validation issues (skip popup during startup auto-check)
            issues_text = "\n".join([f"- {issue.component}: {issue.message}" for issue in result.issues])
            QMessageBox.warning(self, "Validation Issues", f"System validation found issues:\n\n{issues_text}")

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

    def update_config(self, config: AnkiMinerConfig, *, from_settings: bool = False) -> None:
        """Update configuration, save to disk, and propagate to tabs.

        Args:
            config: New configuration.
            from_settings: True when the call originates from the Settings
                save path (``SettingsTab.config_changed`` → here, see app.py).
                In that case SettingsTab and the mining tabs have ALREADY
                received the new config directly via ``config_changed``, so we
                must NOT re-emit ``config_refreshed`` — doing so would re-enter
                ``SettingsTab.update_config`` and reload every panel mid-save.
                Every internal mutation (theme cycle, skip-update, first-run
                flag, post-update version write) leaves it False so SettingsTab
                refreshes and the next Save can't resurrect the stale value.
        """
        self.config = config
        GUIConfigManager.save_config(config)
        # Rebuild config-bound services so AnkiConnect URL/port edits take
        # effect: validation and the undo-delete AnkiService were frozen to the
        # startup config and would otherwise keep hitting the old endpoint.
        self._build_config_bound_services()
        if not from_settings:
            self.config_refreshed.emit(config)

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
        self._anki_service = AnkiService(self.config)

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

        Joins every owned worker thread via the single shutdown join policy
        in :meth:`_join_worker_for_close`; workers that outlive the grace
        join defer the close (see :meth:`_defer_close`) instead of being
        abandoned to Qt teardown.

        Args:
            event: Close event
        """
        laggards: list = []

        def join(worker, *, timeout_ms: int | None = _CLOSE_JOIN_GRACE_MS) -> None:
            if not self._join_worker_for_close(worker, timeout_ms=timeout_ms):
                laggards.append(worker)

        # Window-owned workers: validation, update check, JMdict migration.
        join(self.validation_worker)
        join(self.update_worker)
        join(self._jmdict_migration_worker)

        # The best-effort prewarm worker has no cancel hook (it's a short,
        # uninterruptible cache warm), so join it without timeout instead of
        # routing it through the deferred close: even on a slow dicts_root it
        # exits on its own in bounded time, and a bounded wait(2000) that
        # expired would only delay shutdown behind the poll timer for it.
        join(self._prewarm_worker, timeout_ms=None)

        # Cancel and wait for any processing workers in tabs
        from anki_miner.gui.widgets.youtube_tab import YouTubeTab

        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            # All mining tabs expose their worker on `worker_thread`.
            # DeckBuilderWorker.cancel() also opens its confirm gate, so a worker
            # blocked awaiting Build unblocks and exits.
            join(getattr(tab, "worker_thread", None))
            # YouTube tab owns an additional probe worker; shutdown() tears
            # both threads down cleanly.
            if isinstance(tab, YouTubeTab) and hasattr(tab, "shutdown"):
                tab.shutdown()
            # SettingsTab owns short-lived AnkiConnect workers with no
            # `worker_thread` (T-12). Route each through the same join policy
            # so a long fetch/styling request defers the close instead of being
            # destroyed mid-request.
            iter_workers = getattr(tab, "iter_close_workers", None)
            if callable(iter_workers):
                for worker in iter_workers():
                    join(worker)

        if laggards:
            self._defer_close(event, laggards)
            return

        # Save configuration before closing
        GUIConfigManager.save_config(self.config)
        event.accept()

    def _join_worker_for_close(self, worker, *, timeout_ms: int | None = _CLOSE_JOIN_GRACE_MS) -> bool:
        """Single shutdown join policy for all owned worker threads.

        (T-70 will lift this into a window controller; until then this helper
        is the one place the policy lives — closeEvent only orchestrates.)

        Cancel the worker when it supports ``cancel()``, then join it:

        * ``timeout_ms=None`` — unbounded blocking join, reserved for short
          workers with no cancel hook (the cache prewarm).
        * otherwise — bounded grace join. Returns False when the worker
          outlives it; the caller must then defer the close rather than let
          Qt destroy a running QThread (window-parented workers die with the
          window, unparented tab workers get GC'd — either way Qt6 aborts
          with "QThread: Destroyed while thread is still running" and
          in-flight ffmpeg children are orphaned). Post-cancel runtime today
          is dominated by ffmpeg joins and HTTP timeouts (10-60 s); once
          media-kill (T-33, media_extractor) lands, ``cancel()`` also kills
          ffmpeg and laggards become rare with no changes here.

        Returns True when the worker has exited (or was None / not running).
        """
        if worker is None or not worker.isRunning():
            return True
        cancel = getattr(worker, "cancel", None)
        if callable(cancel):
            cancel()
        if timeout_ms is None:
            worker.wait()
            return True
        return bool(worker.wait(timeout_ms))

    def _defer_close(self, event, laggards: list) -> None:
        """Deferred arm of the shutdown join policy.

        Hides the window (so closing feels instant to the user), refuses the
        close event (so Qt keeps the window — and the running QThreads it
        owns — alive), and polls until every laggard has exited. A worker
        that never exits keeps the hidden process alive by design: a
        discoverable lingering process beats an abort mid-shutdown.
        """
        logger.warning(
            "Deferring close: %d worker thread(s) still running after %d ms grace",
            len(laggards),
            _CLOSE_JOIN_GRACE_MS,
        )
        self._close_laggards = laggards
        if self._close_poll_timer is None:
            self._close_poll_timer = QTimer(self)
            self._close_poll_timer.setInterval(_CLOSE_POLL_INTERVAL_MS)
            self._close_poll_timer.timeout.connect(self._poll_deferred_close)
        self._close_poll_timer.start()
        self.hide()
        event.ignore()

    def _poll_deferred_close(self) -> None:
        """Finish a deferred close once every laggard worker has exited.

        Quits the application explicitly instead of re-entering ``close()``:
        closing an already-hidden window does not reliably emit
        ``lastWindowClosed``, which would leave the event loop running with
        no windows.
        """
        if any(w.isRunning() for w in self._close_laggards):
            return
        if self._close_poll_timer is not None:
            self._close_poll_timer.stop()
        GUIConfigManager.save_config(self.config)
        QApplication.quit()

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
