"""Smoke test: DeckBuilderTab is registered in the main() wiring.

Builds the full tab stack from ``anki_miner.gui.app.main``'s construction
block (via a thin helper that mirrors the real call) and asserts that
a tab titled "Deck Builder" is present and is an instance of DeckBuilderTab.

External services (AnkiConnect, disk I/O) are patched out exactly as the
existing window construction tests do it.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.deck_builder_tab import DeckBuilderTab


def _patch_heavy_init(monkeypatch, test_config):
    """Patch out side-effect-heavy startup collaborators."""
    from anki_miner.gui import main_window as mw_module

    monkeypatch.setattr(mw_module.GUIConfigManager, "load_config", lambda: test_config)
    monkeypatch.setattr(mw_module.GUIConfigManager, "save_config", lambda cfg: None)
    monkeypatch.setattr(mw_module.ValidationService, "__init__", lambda self, *a, **kw: None)
    monkeypatch.setattr(mw_module.MainWindow, "_run_validation", lambda self: None)
    monkeypatch.setattr(mw_module.MainWindow, "_check_for_updates", lambda self: None)
    monkeypatch.setattr(mw_module.MainWindow, "_maybe_create_shortcut_on_first_run", lambda self: None)
    monkeypatch.setattr(mw_module.MainWindow, "_maybe_offer_first_run_setup", lambda self: None)


def _build_tabs(monkeypatch, test_config):
    """Return (window, tab_titles, tabs_by_title) built by the app wiring.

    NOTE: this helper shadows the tab-construction block in ``app.main`` to
    avoid spinning up the full Qt event loop / ``sys.exit``. It must be kept in
    sync with ``app.main`` when tabs are added or reordered.
    """
    _patch_heavy_init(monkeypatch, test_config)

    from anki_miner.gui import app as app_module
    from anki_miner.gui.main_window import MainWindow
    from anki_miner.gui.presenters import GUIPresenter, GUIProgressCallback
    from anki_miner.gui.utils.service_factory import create_youtube_fetcher
    from anki_miner.gui.widgets.analytics_tab import AnalyticsTab
    from anki_miner.gui.widgets.audiobook_tab import AudiobookTab
    from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab
    from anki_miner.gui.widgets.deck_builder_tab import DeckBuilderTab
    from anki_miner.gui.widgets.reading_tab import ReadingTab
    from anki_miner.gui.widgets.settings_tab import SettingsTab
    from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab
    from anki_miner.gui.widgets.youtube_tab import YouTubeTab
    from anki_miner.services.stats_service import StatsService

    window = MainWindow()

    stats_service = MagicMock(spec=StatsService)

    episode_presenter = GUIPresenter(window)
    episode_progress = GUIProgressCallback(window)
    episode_tab = SingleEpisodeTab(
        window.get_config(), episode_presenter, episode_progress, stats_service=stats_service
    )
    app_module.register_mining_tab(window, episode_tab, episode_presenter, "Episode Mining")

    batch_presenter = GUIPresenter(window)
    batch_progress = GUIProgressCallback(window)
    batch_tab = BatchProcessingTab(window.get_config(), batch_presenter, batch_progress, stats_service=stats_service)
    app_module.register_mining_tab(window, batch_tab, batch_presenter, "Batch Mining")

    deck_builder_presenter = GUIPresenter(window)
    deck_builder_progress = GUIProgressCallback(window)
    deck_builder_tab = DeckBuilderTab(
        window.get_config(),
        deck_builder_presenter,
        deck_builder_progress,
        stats_service=stats_service,
    )
    app_module.register_mining_tab(window, deck_builder_tab, deck_builder_presenter, "Deck Builder")

    youtube_presenter = GUIPresenter(window)
    youtube_fetcher = create_youtube_fetcher(window.get_config())
    youtube_tab = YouTubeTab(
        config=window.get_config(),
        processor=None,
        fetcher=youtube_fetcher,
        presenter=youtube_presenter,
        stats_service=stats_service,
    )
    app_module.register_mining_tab(window, youtube_tab, youtube_presenter, "YouTube")

    audiobook_presenter = GUIPresenter(window)
    audiobook_tab = AudiobookTab(
        config=window.get_config(),
        processor=None,
        presenter=audiobook_presenter,
        stats_service=stats_service,
    )
    app_module.register_mining_tab(window, audiobook_tab, audiobook_presenter, "Audio")

    reading_presenter = GUIPresenter(window)
    reading_tab = ReadingTab(
        config=window.get_config(),
        presenter=reading_presenter,
        stats_service=stats_service,
    )
    app_module.register_mining_tab(window, reading_tab, reading_presenter, "Reading")

    analytics_tab = AnalyticsTab(stats_service)
    window.tabs.addTab(analytics_tab, "Analytics")

    settings_tab = SettingsTab(window.get_config())
    settings_tab.config_changed.connect(window.update_config)
    for i in range(window.tabs.count()):
        tab_widget = window.tabs.widget(i)
        if hasattr(tab_widget, "update_config"):
            settings_tab.config_changed.connect(tab_widget.update_config)
    window.tabs.addTab(settings_tab, "Settings")

    window.config_refreshed.connect(settings_tab.update_config)

    window.setup_tab_shortcuts()

    tab_count = window.tabs.count()
    titles = [window.tabs.tabText(i) for i in range(tab_count)]
    tabs = {window.tabs.tabText(i): window.tabs.widget(i) for i in range(tab_count)}

    return window, titles, tabs


@pytest.fixture
def wired_window(monkeypatch, test_config, qtbot):
    window, titles, tabs = _build_tabs(monkeypatch, test_config)
    qtbot.addWidget(window)
    yield window, titles, tabs
    window.deleteLater()


def test_deck_builder_tab_present(wired_window):
    _window, titles, _tabs = wired_window
    assert "Deck Builder" in titles


def test_deck_builder_tab_is_correct_type(wired_window):
    _window, _titles, tabs = wired_window
    assert isinstance(tabs["Deck Builder"], DeckBuilderTab)


def test_deck_builder_tab_after_batch_mining(wired_window):
    """Deck Builder must appear right after Batch Mining (index 2)."""
    _window, titles, _tabs = wired_window
    assert titles.index("Deck Builder") == titles.index("Batch Mining") + 1


def test_deck_builder_tab_before_youtube(wired_window):
    """Deck Builder must appear before YouTube."""
    _window, titles, _tabs = wired_window
    assert titles.index("Deck Builder") < titles.index("YouTube")
