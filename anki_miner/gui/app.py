"""Main GUI application entry point."""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Protocol, runtime_checkable

from PyQt6.QtCore import QCoreApplication, Qt, QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QWidget

from anki_miner.config import AnkiMinerConfig
from anki_miner.config.paths import ANKI_MINER_HOME
from anki_miner.gui.i18n import install_translators
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

logger = logging.getLogger(__name__)


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


def _configure_logging(log_path: Path) -> None:
    """Attach (or re-point) a RotatingFileHandler on the root logger.

    Called from main() so all modules that already call
    ``logging.getLogger(__name__)`` have their records captured to disk.
    Two 2 MB backup files → at most ~6 MB on disk at any time.

    Idempotent: a handler attached by a previous call is removed and replaced,
    so calling this twice — bootstrap default-path → config-path re-point (F3),
    or a second ``main()``/in-process re-launch (test/E2E harness) — never stacks
    handlers writing each record N times (F5).
    """
    log_path = Path(log_path)  # tolerate a str caller; .parent below needs a Path
    root = logging.getLogger()
    # Drop the handler we previously attached so a re-point / re-call doesn't
    # duplicate it. Tagged with a sentinel attribute to avoid removing handlers
    # installed by anything else.
    for existing in list(root.handlers):
        if getattr(existing, "_anki_miner_sink", False):
            root.removeHandler(existing)
            existing.close()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_path,
        maxBytes=2 * 1024 * 1024,
        backupCount=2,
        encoding="utf-8",
        delay=True,
    )
    handler.setLevel(logging.DEBUG)
    handler._anki_miner_sink = True  # type: ignore[attr-defined]  # sentinel for idempotent replacement
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    handler.setFormatter(fmt)
    # Root logger at WARNING so third-party libs (yt-dlp, fugashi, …) only
    # write WARNING+ to the file; the project namespace gets full DEBUG coverage.
    # A record must clear both its logger's effective level AND the handler's
    # level — setting the handler to DEBUG here means the handler itself never
    # silences anything; filtering happens at the logger level.
    root.setLevel(logging.WARNING)
    root.addHandler(handler)
    logging.getLogger("anki_miner").setLevel(logging.DEBUG)


@runtime_checkable
class _HasUpdateConfig(Protocol):
    """Structural type for tab widgets that accept config updates."""

    def update_config(self, config: AnkiMinerConfig) -> None: ...


def register_mining_tab(window: "MainWindow", tab: "_HasUpdateConfig", presenter: "GUIPresenter", label: str) -> None:
    """Register a mining tab and wire its presenter to the main window.

    One call replaces the hand-repeated boilerplate that used to appear at
    three separate sites in ``main()``:

    1. ``window.tabs.addTab(tab, label)``
    2. Six presenter-signal → ``window._on_*`` handler connections.
    3. ``window.config_refreshed`` → ``tab.update_config`` (non-settings refreshes,
       e.g. JMdict migration finishing in the background).

    The ``settings_tab.config_changed`` → ``tab.update_config`` connection is NOT
    wired here because ``SettingsTab`` does not yet exist when mining tabs are
    registered.  That connection is handled at ``SettingsTab`` construction time
    in ``main()`` — it iterates over ``window.tabs`` (excluding the Settings tab
    itself) to avoid repeating every tab name.

    Args:
        window: The :class:`MainWindow` instance.
        tab: The tab widget to add; must expose ``update_config``.
        presenter: The :class:`GUIPresenter` for this tab.
        label: The text label for the tab.
    """
    assert isinstance(tab, QWidget), "tab must be a QWidget"

    window.tabs.addTab(tab, label)

    presenter.info_signal.connect(window._on_info_message)
    presenter.success_signal.connect(window._on_success_message)
    presenter.warning_signal.connect(window._on_warning_message)
    presenter.error_signal.connect(window._on_error_message)
    presenter.processing_result_signal.connect(window._on_processing_result)
    presenter.word_preview_signal.connect(window._on_word_preview)

    window.config_refreshed.connect(tab.update_config)


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

    # Attach the rotating file handler to the DEFAULT path before loading config
    # so config-load diagnostics — including the OVH-001 .bak-recovery warnings
    # emitted inside load_config — are captured: those warnings fire as soon as a
    # handler exists, so attaching here (before the load) is what makes them land
    # in the file rather than going nowhere (F3).
    # GUIConfigManager has no Qt dependency, so it can run before QApplication.
    _default_log_path = ANKI_MINER_HOME / "anki_miner.log"
    _configure_logging(_default_log_path)
    try:
        _early_config = GUIConfigManager.load_config()
        _log_path = _early_config.log_path
    except Exception:
        logger.exception("Failed to load config at startup; using default log path")
        _log_path = _default_log_path
    # Honour a user-customised log_path by re-pointing the handler (idempotent,
    # so no duplicate sink). No-op in the common case where it equals the default.
    if _log_path != _default_log_path:
        _configure_logging(_log_path)

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

    # Install UI translators BEFORE any widget is built — widgets capture their
    # tr() strings at construction time, and language is restart-to-apply (no
    # live retranslateUi). Stash on `app` so the translators outlive this call.
    app._translators = install_translators(app, _early_config.ui_language)  # type: ignore[attr-defined]

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

    # Create per-tab presenters and progress callbacks to avoid cross-tab signal pollution.
    # register_mining_tab() handles: addTab + six presenter-signal connections +
    # window.config_refreshed → tab.update_config.
    episode_presenter = GUIPresenter(window)
    episode_progress = GUIProgressCallback(window)
    episode_tab = SingleEpisodeTab(
        window.get_config(),
        episode_presenter,
        episode_progress,
        stats_service=stats_service,
    )
    register_mining_tab(
        window, episode_tab, episode_presenter, QCoreApplication.translate("MainWindow", "Episode Mining")
    )

    batch_presenter = GUIPresenter(window)
    batch_progress = GUIProgressCallback(window)
    batch_tab = BatchProcessingTab(
        window.get_config(),
        batch_presenter,
        batch_progress,
        stats_service=stats_service,
    )
    register_mining_tab(window, batch_tab, batch_presenter, QCoreApplication.translate("MainWindow", "Batch Mining"))

    deck_builder_presenter = GUIPresenter(window)
    deck_builder_progress = GUIProgressCallback(window)
    deck_builder_tab = DeckBuilderTab(
        window.get_config(),
        deck_builder_presenter,
        deck_builder_progress,
        stats_service=stats_service,
    )
    register_mining_tab(
        window, deck_builder_tab, deck_builder_presenter, QCoreApplication.translate("MainWindow", "Deck Builder")
    )

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
    register_mining_tab(window, youtube_tab, youtube_presenter, QCoreApplication.translate("MainWindow", "YouTube"))

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
    register_mining_tab(
        window, audiobook_tab, audiobook_presenter, QCoreApplication.translate("MainWindow", "Audiobook")
    )

    # Analytics tab (non-mining: no presenter, no update_config wiring)
    analytics_tab = AnalyticsTab(stats_service)
    window.tabs.addTab(analytics_tab, QCoreApplication.translate("MainWindow", "Analytics"))

    settings_tab = SettingsTab(window.get_config())
    # from_settings=True suppresses the config_refreshed re-emit: SettingsTab
    # and the mining tabs are notified directly on the next lines, so a
    # re-emit would only reload SettingsTab's panels mid-save (re-entrancy).
    settings_tab.config_changed.connect(lambda cfg: window.update_config(cfg, from_settings=True))
    # Wire config_changed to every mining tab registered via register_mining_tab.
    # Iterating over window.tabs (skipping Analytics and Settings themselves)
    # avoids repeating each tab name here.
    for i in range(window.tabs.count()):
        tab_widget = window.tabs.widget(i)
        if tab_widget is not None and hasattr(tab_widget, "update_config"):
            settings_tab.config_changed.connect(tab_widget.update_config)
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
    window.tabs.addTab(settings_tab, QCoreApplication.translate("MainWindow", "Settings"))

    # Non-Settings config refreshes (e.g. JMdict migration finishing in the
    # background) must propagate to SettingsTab so its panels don't go stale.
    # Mining tabs are already wired via register_mining_tab's config_refreshed
    # connection.
    window.config_refreshed.connect(settings_tab.update_config)

    # All tabs are now registered — create the count-driven Ctrl+N shortcuts.
    # This must come AFTER all addTab calls so self.tabs.count() is final.
    window.setup_tab_shortcuts()

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
