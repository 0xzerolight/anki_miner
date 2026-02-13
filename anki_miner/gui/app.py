"""Main GUI application entry point."""

import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from anki_miner.gui.main_window import MainWindow
from anki_miner.gui.presenters import GUIPresenter, GUIProgressCallback
from anki_miner.gui.resources import get_resource_dir
from anki_miner.gui.resources.styles.theme import Theme
from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab
from anki_miner.gui.widgets.settings_tab import SettingsTab
from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab


def main():
    """Launch the Anki Miner GUI application."""
    # Enable high DPI scaling
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    # Create application
    app = QApplication(sys.argv)
    app.setApplicationName("Anki Miner")
    app.setOrganizationName("AnkiMiner")

    # Set application icon
    icon_path = get_resource_dir() / "icons" / "anki_miner.svg"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    # Initialize theme system
    theme = Theme.get_instance()

    # Apply stylesheet
    stylesheet = theme.get_stylesheet()
    app.setStyleSheet(stylesheet)

    # Create main window
    window = MainWindow()

    # Create per-tab presenters and progress callbacks to avoid cross-tab signal pollution
    episode_presenter = GUIPresenter(window)
    episode_progress = GUIProgressCallback(window)
    episode_tab = SingleEpisodeTab(window.get_config(), episode_presenter, episode_progress)
    window.tabs.addTab(episode_tab, "Episode Mining")

    batch_presenter = GUIPresenter(window)
    batch_progress = GUIProgressCallback(window)
    batch_tab = BatchProcessingTab(window.get_config(), batch_presenter, batch_progress)
    window.tabs.addTab(batch_tab, "Batch Mining")

    # Connect both tab presenters to MainWindow status bar handlers
    for presenter in (episode_presenter, batch_presenter):
        presenter.info_signal.connect(window._on_info_message)
        presenter.success_signal.connect(window._on_success_message)
        presenter.warning_signal.connect(window._on_warning_message)
        presenter.error_signal.connect(window._on_error_message)
        presenter.processing_result_signal.connect(window._on_processing_result)
        presenter.word_preview_signal.connect(window._on_word_preview)

    settings_tab = SettingsTab(window.get_config())
    settings_tab.config_changed.connect(window.update_config)
    settings_tab.config_changed.connect(episode_tab.update_config)
    settings_tab.config_changed.connect(batch_tab.update_config)
    window.tabs.addTab(settings_tab, "Settings")

    # Show window
    window.show()

    # Run event loop
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
