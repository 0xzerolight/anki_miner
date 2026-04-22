"""Main GUI application entry point."""

import os
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from anki_miner.gui.main_window import MainWindow
from anki_miner.gui.presenters import GUIPresenter, GUIProgressCallback
from anki_miner.gui.resources import get_resource_dir
from anki_miner.gui.resources.styles.theme import Theme
from anki_miner.gui.utils.service_factory import (
    create_episode_processor,
    create_youtube_fetcher,
)
from anki_miner.gui.widgets.analytics_tab import AnalyticsTab
from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab
from anki_miner.gui.widgets.settings_tab import SettingsTab
from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab
from anki_miner.gui.widgets.youtube_tab import YouTubeTab
from anki_miner.services.stats_service import StatsService


def _run_bundled_smoke() -> int:
    """Env-var-gated smoke path for PyInstaller bundle validation.

    Triggered by ANKI_MINER_SMOKE=youtube. Verifies yt-dlp extractors were
    correctly bundled by probing a public short video. Not a CLI surface —
    the flag is hidden, env-var-only, and exits before any Qt init.
    """
    from anki_miner.config.config import AnkiMinerConfig
    from anki_miner.services.youtube_fetcher import YouTubeFetcherService

    fetcher = YouTubeFetcherService(AnkiMinerConfig())
    try:
        info = fetcher.probe_metadata("https://www.youtube.com/watch?v=9bZkp7q19f0")
    except Exception as exc:
        print(f"BUNDLED_SMOKE_FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"BUNDLED_SMOKE_PASS: id={info.video_id}")
    return 0


def main():
    """Launch the Anki Miner GUI application."""
    # Env-var-gated smoke path (PyInstaller bundled-binary validation).
    # Runs before Qt init so headless CI can verify yt-dlp extractor
    # bundling without spinning up a display.
    if os.environ.get("ANKI_MINER_SMOKE") == "youtube":
        sys.exit(_run_bundled_smoke())

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

    # Initialize theme system and apply stylesheet + palette
    Theme.get_instance()
    Theme.apply_to_app(app)

    # Create main window
    window = MainWindow()

    # Initialize stats service for analytics
    stats_service = StatsService(window.get_config().stats_db_path)
    stats_service.load()

    # Create per-tab presenters and progress callbacks to avoid cross-tab signal pollution
    episode_presenter = GUIPresenter(window)
    episode_progress = GUIProgressCallback(window)
    episode_tab = SingleEpisodeTab(
        window.get_config(),
        episode_presenter,
        episode_progress,
        stats_service=stats_service,
    )
    window.tabs.addTab(episode_tab, "Episode Mining")

    batch_presenter = GUIPresenter(window)
    batch_progress = GUIProgressCallback(window)
    batch_tab = BatchProcessingTab(
        window.get_config(),
        batch_presenter,
        batch_progress,
        stats_service=stats_service,
    )
    window.tabs.addTab(batch_tab, "Batch Mining")

    # Connect both tab presenters to MainWindow status bar handlers
    for presenter in (episode_presenter, batch_presenter):
        presenter.info_signal.connect(window._on_info_message)
        presenter.success_signal.connect(window._on_success_message)
        presenter.warning_signal.connect(window._on_warning_message)
        presenter.error_signal.connect(window._on_error_message)
        presenter.processing_result_signal.connect(window._on_processing_result)
        presenter.word_preview_signal.connect(window._on_word_preview)

    # YouTube tab (uses its own presenter + shared stats service via processor)
    youtube_presenter = GUIPresenter(window)
    youtube_fetcher = create_youtube_fetcher(window.get_config())
    youtube_processor = create_episode_processor(
        window.get_config(),
        youtube_presenter,
        stats_service=stats_service,
    )
    youtube_tab = YouTubeTab(
        config=window.get_config(),
        processor=youtube_processor,
        fetcher=youtube_fetcher,
        presenter=youtube_presenter,
    )
    window.tabs.addTab(youtube_tab, "YouTube")

    # Route YouTube tab presenter through the main window status bar handlers
    youtube_presenter.info_signal.connect(window._on_info_message)
    youtube_presenter.success_signal.connect(window._on_success_message)
    youtube_presenter.warning_signal.connect(window._on_warning_message)
    youtube_presenter.error_signal.connect(window._on_error_message)
    youtube_presenter.processing_result_signal.connect(window._on_processing_result)
    youtube_presenter.word_preview_signal.connect(window._on_word_preview)

    # Analytics tab
    analytics_tab = AnalyticsTab(stats_service)
    window.tabs.addTab(analytics_tab, "Analytics")

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
