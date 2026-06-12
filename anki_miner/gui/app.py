"""Main GUI application entry point."""

import os
import sys

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from anki_miner.gui.main_window import MainWindow
from anki_miner.gui.presenters import GUIPresenter, GUIProgressCallback
from anki_miner.gui.resources import get_resource_dir
from anki_miner.gui.resources.styles.theme import Theme
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.utils.service_factory import create_youtube_fetcher
from anki_miner.gui.widgets.analytics_tab import AnalyticsTab
from anki_miner.gui.widgets.audiobook_tab import AudiobookTab
from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab
from anki_miner.gui.widgets.deck_builder_tab import DeckBuilderTab
from anki_miner.gui.widgets.settings_tab import SettingsTab
from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab
from anki_miner.gui.widgets.youtube_tab import YouTubeTab
from anki_miner.services.stats_service import StatsService


def _scrub_pyinstaller_env() -> None:
    # PyInstaller's bootloader prepends _internal/ to LD_LIBRARY_PATH so
    # bundled libs load at startup. That value leaks into every subprocess
    # we spawn (yt-dlp, ffmpeg), where it shadows the host's newer OpenSSL
    # with our older bundled libcrypto and breaks system binaries linked
    # against OpenSSL >= 3.1. Restore the pre-launch value before anything
    # else runs.
    # https://pyinstaller.org/en/stable/runtime-information.html
    if not getattr(sys, "frozen", False):
        return
    for var in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        orig = os.environ.pop(f"{var}_ORIG", None)
        if orig is not None:
            os.environ[var] = orig
        else:
            os.environ.pop(var, None)


def _run_bundled_smoke() -> int:
    """Env-var-gated smoke path for PyInstaller bundle validation.

    Triggered by ANKI_MINER_SMOKE=youtube. Verifies yt-dlp and its extractor
    registry survived PyInstaller's collect_all by walking the registry
    offline and resolving the Youtube extractor. No network, no YoutubeDL,
    no bot challenge. Not a CLI surface — the flag is hidden, env-var-only,
    and exits before any Qt init.
    """
    try:
        from yt_dlp.extractor import (  # type: ignore[import-untyped]
            gen_extractors,
            get_info_extractor,
        )

        extractor_count = sum(1 for _ in gen_extractors())
        if extractor_count < 1000:
            raise RuntimeError(
                f"extractor registry shrunk: {extractor_count} < 1000 "
                "(expected ~1600; PyInstaller collect_all may have dropped extractors)"
            )

        youtube_ie = get_info_extractor("Youtube")
        if youtube_ie is None:
            raise RuntimeError("Youtube extractor not resolvable from bundle")

        if not youtube_ie.suitable("https://www.youtube.com/watch?v=9bZkp7q19f0"):
            raise RuntimeError("YoutubeIE.suitable() rejected a canonical YouTube URL")
    except Exception as exc:
        print(f"BUNDLED_SMOKE_FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"BUNDLED_SMOKE_PASS: yt_dlp extractors={extractor_count}")
    return 0


def _connect_settings_validation(window: MainWindow, settings_tab: SettingsTab) -> None:
    """Connect the Settings tab's validation requests to the window (T-53).

    ``SettingsTab.validation_requested`` is emitted by Test Connection and the
    deck/note-type sync buttons (the Anki panel forwards all three into it).
    It was declared and forwarded but never connected, so those buttons did
    nothing and the connection badge stuck at "Checking connection...". Wiring
    it to ``_run_validation`` runs a validation pass; the result flows back
    through ``_on_validation_result``, which now updates the badge.

    Extracted from ``main()`` so the connection is unit-testable without
    standing up the whole app.
    """
    settings_tab.validation_requested.connect(window._run_validation)


def main():
    """Launch the Anki Miner GUI application."""
    _scrub_pyinstaller_env()

    # Env-var-gated smoke path (PyInstaller bundled-binary validation).
    # Runs before Qt init so headless CI can verify yt-dlp extractor
    # bundling without spinning up a display.
    if os.environ.get("ANKI_MINER_SMOKE") == "youtube":
        sys.exit(_run_bundled_smoke())

    # Enable high DPI scaling
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    # Create application
    app = QApplication(sys.argv)
    app.setApplicationName("Anki Miner")
    app.setOrganizationName("AnkiMiner")

    # Set application icon
    icon_path = get_resource_dir() / "icons" / "anki_miner.svg"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    # Seed the theme singleton from gui_config.json so the initial paint uses
    # the right active theme and the favorites combo is correctly populated.
    # MainWindow re-loads the same config a moment later (idempotent).
    _initial_config = GUIConfigManager.load_config()
    Theme.initialize(
        active=_initial_config.theme,
        favorites=_initial_config.theme_favorites,
        user_dir=_initial_config.themes_root,
        font_scale=_initial_config.ui_font_scale,
    )
    Theme.apply_to_app(app)

    # Create main window
    window = MainWindow()

    # Initialize stats service for analytics. ``.load()`` opens the SQLite
    # file; defer to after window.show() so the empty shell paints first
    # and the user sees feedback while disk I/O finishes.
    stats_service = StatsService(window.get_config().stats_db_path)

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

    deck_builder_presenter = GUIPresenter(window)
    deck_builder_progress = GUIProgressCallback(window)
    deck_builder_tab = DeckBuilderTab(
        window.get_config(),
        deck_builder_presenter,
        deck_builder_progress,
        stats_service=stats_service,
    )
    window.tabs.addTab(deck_builder_tab, "Deck Builder")

    # Connect mining-tab presenters to MainWindow status bar handlers
    for presenter in (episode_presenter, batch_presenter, deck_builder_presenter):
        presenter.info_signal.connect(window._on_info_message)
        presenter.success_signal.connect(window._on_success_message)
        presenter.warning_signal.connect(window._on_warning_message)
        presenter.error_signal.connect(window._on_error_message)
        presenter.processing_result_signal.connect(window._on_processing_result)
        presenter.word_preview_signal.connect(window._on_word_preview)

    # YouTube tab (uses its own presenter + shared stats service). The
    # processor is built lazily on the first Mine click so the dictionary
    # chain — which opens every installed dict's sqlite — does not block
    # the initial window paint. ``stats_service`` is threaded through so
    # mining sessions still land in analytics regardless of when the
    # processor materializes.
    youtube_presenter = GUIPresenter(window)
    youtube_fetcher = create_youtube_fetcher(window.get_config())
    youtube_tab = YouTubeTab(
        config=window.get_config(),
        processor=None,
        fetcher=youtube_fetcher,
        presenter=youtube_presenter,
        stats_service=stats_service,
    )
    window.tabs.addTab(youtube_tab, "YouTube")

    # Route YouTube tab presenter through the main window status bar handlers
    youtube_presenter.info_signal.connect(window._on_info_message)
    youtube_presenter.success_signal.connect(window._on_success_message)
    youtube_presenter.warning_signal.connect(window._on_warning_message)
    youtube_presenter.error_signal.connect(window._on_error_message)
    youtube_presenter.processing_result_signal.connect(window._on_processing_result)
    youtube_presenter.word_preview_signal.connect(window._on_word_preview)

    # Audiobook tab (Issue #71). Same lazy-processor pattern as YouTube:
    # processor=None defers the dictionary-chain build to the first Mine
    # click; stats_service is threaded through so sessions land in analytics.
    audiobook_presenter = GUIPresenter(window)
    audiobook_tab = AudiobookTab(
        config=window.get_config(),
        processor=None,
        presenter=audiobook_presenter,
        stats_service=stats_service,
    )
    window.tabs.addTab(audiobook_tab, "Audiobook")

    # Route Audiobook tab presenter through the main window status bar handlers
    audiobook_presenter.info_signal.connect(window._on_info_message)
    audiobook_presenter.success_signal.connect(window._on_success_message)
    audiobook_presenter.warning_signal.connect(window._on_warning_message)
    audiobook_presenter.error_signal.connect(window._on_error_message)
    audiobook_presenter.processing_result_signal.connect(window._on_processing_result)
    audiobook_presenter.word_preview_signal.connect(window._on_word_preview)

    # Analytics tab
    analytics_tab = AnalyticsTab(stats_service)
    window.tabs.addTab(analytics_tab, "Analytics")

    settings_tab = SettingsTab(window.get_config())
    # from_settings=True suppresses the config_refreshed re-emit: SettingsTab
    # and the mining tabs are notified directly on the next four lines, so a
    # re-emit would only reload SettingsTab's panels mid-save (re-entrancy).
    settings_tab.config_changed.connect(lambda cfg: window.update_config(cfg, from_settings=True))
    settings_tab.config_changed.connect(episode_tab.update_config)
    settings_tab.config_changed.connect(batch_tab.update_config)
    settings_tab.config_changed.connect(deck_builder_tab.update_config)
    settings_tab.config_changed.connect(youtube_tab.update_config)
    settings_tab.config_changed.connect(audiobook_tab.update_config)
    # Make Test Connection + the deck/note-type sync buttons live: they all
    # emit SettingsTab.validation_requested, which was previously connected to
    # nothing (T-53). Routing it to _run_validation also drives the Anki
    # connection badge via _on_validation_result.
    _connect_settings_validation(window, settings_tab)
    # Wire the Dictionary Settings panel's pre-remove hook so deleting a
    # dictionary closes cached sqlite handles across every tab first — Win11
    # rejects the rmtree otherwise (Issue #30).
    settings_tab.dictionary_panel.set_release_callback(window.release_dictionary_resources)
    # Favorites-list edits in Themes panel must repopulate the top-right combo
    # immediately; the panel doesn't know about the header so the wiring lives
    # here. Active-theme changes from the panel must update the selected entry
    # in the combo without re-emitting `theme_changed` (the theme is already
    # applied — re-emitting would loop back through `_on_theme_changed`).
    settings_tab.themes_panel.favorites_changed.connect(window.header.refresh_favorites)
    settings_tab.themes_panel.state_changed.connect(lambda *_: window.header.update_theme_selector())
    window.tabs.addTab(settings_tab, "Settings")

    # Non-Settings config refreshes (e.g. JMdict migration finishing in the
    # background) must propagate to tabs that cache services. Without this,
    # the first-launch user who needs the legacy XML migrated would see all
    # lookups go to Jisho until the next restart.
    window.config_refreshed.connect(settings_tab.update_config)
    window.config_refreshed.connect(episode_tab.update_config)
    window.config_refreshed.connect(batch_tab.update_config)
    window.config_refreshed.connect(deck_builder_tab.update_config)
    window.config_refreshed.connect(youtube_tab.update_config)
    window.config_refreshed.connect(audiobook_tab.update_config)

    # Show window first so the user sees the UI immediately; then run the
    # deferred init (stats DB open) on the next event loop tick. The
    # YouTube tab's episode processor is built even lazier — on first
    # Mine click — because the dictionary chain dominates startup cost.
    window.show()
    QTimer.singleShot(0, stats_service.load)

    # Pre-warm the shared MeCab tagger (get_shared_tagger) AND the dictionary
    # chain off the GUI thread, scheduled on the next event-loop tick so it
    # never blocks the first paint. The first Mine builds these on the GUI
    # thread today, freezing the UI for seconds; warming them in the background
    # makes that first real Mine materially faster. The worker warms the SHARED
    # tagger singleton that mining reuses (it builds its own sqlite connections
    # for the dict chain and discards those — connections are unsafe across
    # threads). Best-effort: clicking Mine before it finishes simply takes
    # today's cold path. The window's background-task controller holds the
    # reference (so the QThread isn't GC'd mid-run and shutdown can join it)
    # and clears it once the built-in ``finished`` signal fires.
    def _start_prewarm() -> None:
        from anki_miner.gui.workers.prewarm_worker import PrewarmWorker

        worker = PrewarmWorker(window.get_config())
        window.background_tasks.set_prewarm(worker)
        worker.start()

    QTimer.singleShot(0, _start_prewarm)

    # Run event loop
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
