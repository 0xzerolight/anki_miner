"""Shared fixtures for the ``tests/unit`` GUI/app-wiring suites.

``patch_heavy_init`` is the single, parametrizable replacement for the 21
hand-copied ``_patch_heavy_init`` variants that used to live in
``test_app_*`` and ``test_main_window_*`` modules. It stubs out the
side-effect-heavy collaborators ``MainWindow.__init__`` reaches on startup so a
real ``MainWindow()`` can be constructed synchronously (no AnkiConnect, no disk
I/O, no auto-update / first-run modals, no background QThreads).

Members the individual files diverged on are exposed as opt-outs:

* ``stub_run_validation`` (default ``True``) — leave ``False`` when the test
  wants the real ``_run_validation`` (e.g. it installs its own recorder, or the
  wiring under test drives validation itself).
* ``stub_first_run_setup`` (default ``True``) — leave ``False`` when
  ``_maybe_offer_first_run_setup`` is the method under test (it is safe to leave
  real because its trigger is a ``QTimer.singleShot(0, ...)`` that only fires
  once an event loop spins, which synchronous unit tests never do).

File-specific extras (e.g. patching ``run_off_thread`` or a settings panel's
async re-probe) are NOT baked in here — the owning test applies them with its
own ``monkeypatch`` on top of this call.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def patch_heavy_init(monkeypatch):
    """Return ``apply(config, *, stub_run_validation=True, stub_first_run_setup=True)``.

    ``config`` becomes the value ``GUIConfigManager.load_config`` returns, so
    callers pass whatever ``AnkiMinerConfig`` the window should construct with
    (the shared ``test_config`` fixture, or a ``replace(...)``-tweaked copy).
    """

    def _apply(config, *, stub_run_validation: bool = True, stub_first_run_setup: bool = True) -> None:
        from anki_miner.gui import main_window as mw_module

        monkeypatch.setattr(mw_module.GUIConfigManager, "load_config", lambda: config)
        monkeypatch.setattr(mw_module.GUIConfigManager, "save_config", lambda cfg: None)
        monkeypatch.setattr(mw_module.ValidationService, "__init__", lambda self, *a, **kw: None)
        monkeypatch.setattr(mw_module.MainWindow, "_check_for_updates", lambda self: None)
        monkeypatch.setattr(mw_module.MainWindow, "_maybe_create_shortcut_on_first_run", lambda self: None)
        if stub_run_validation:
            monkeypatch.setattr(mw_module.MainWindow, "_run_validation", lambda self: None)
        if stub_first_run_setup:
            monkeypatch.setattr(mw_module.MainWindow, "_maybe_offer_first_run_setup", lambda self: None)

    return _apply


def _build_tabs(patch_heavy_init, test_config):
    """Return (window, tab_titles, tabs_by_title) built by the app wiring.

    NOTE: this helper shadows the tab-construction block in ``app.main`` to
    avoid spinning up the full Qt event loop / ``sys.exit``. It must be kept in
    sync with ``app.main`` when tabs are added or reordered.
    """
    patch_heavy_init(test_config)

    from anki_miner.gui import app as app_module
    from anki_miner.gui.main_window import MainWindow
    from anki_miner.gui.presenters import GUIPresenter, GUIProgressCallback
    from anki_miner.gui.utils.service_factory import create_youtube_fetcher
    from anki_miner.gui.widgets.analytics_tab import AnalyticsTab
    from anki_miner.gui.widgets.audiobook_tab import AudiobookTab
    from anki_miner.gui.widgets.deck_builder_tab import DeckBuilderTab
    from anki_miner.gui.widgets.reading_tab import ReadingTab
    from anki_miner.gui.widgets.settings_tab import SettingsTab
    from anki_miner.gui.widgets.video_tab import VideoTab
    from anki_miner.services.stats_service import StatsService

    window = MainWindow()

    stats_service = MagicMock(spec=StatsService)

    episode_presenter = GUIPresenter(window)
    episode_progress = GUIProgressCallback(window)
    batch_presenter = GUIPresenter(window)
    batch_progress = GUIProgressCallback(window)
    youtube_presenter = GUIPresenter(window)
    youtube_fetcher = create_youtube_fetcher(window.get_config())
    video_tab = VideoTab(
        window.get_config(),
        episode_presenter=episode_presenter,
        episode_progress=episode_progress,
        batch_presenter=batch_presenter,
        batch_progress=batch_progress,
        youtube_presenter=youtube_presenter,
        youtube_fetcher=youtube_fetcher,
        stats_service=stats_service,
    )
    app_module.register_mining_tab(
        window,
        video_tab,
        episode_presenter,
        "Video",
        extra_presenters=(batch_presenter, youtube_presenter),
    )

    deck_builder_presenter = GUIPresenter(window)
    deck_builder_progress = GUIProgressCallback(window)
    deck_builder_tab = DeckBuilderTab(
        window.get_config(),
        deck_builder_presenter,
        deck_builder_progress,
        stats_service=stats_service,
    )
    app_module.register_mining_tab(window, deck_builder_tab, deck_builder_presenter, "Deck Builder")

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
    settings_tab.config_changed.connect(lambda cfg: window.update_config(cfg, from_settings=True))
    window.tabs.addTab(settings_tab, "Settings")

    window.config_refreshed.connect(settings_tab.update_config)

    window.setup_tab_shortcuts()

    tab_count = window.tabs.count()
    titles = [window.tabs.tabText(i) for i in range(tab_count)]
    tabs = {window.tabs.tabText(i): window.tabs.widget(i) for i in range(tab_count)}

    return window, titles, tabs


@pytest.fixture
def wired_window(patch_heavy_init, test_config, qtbot):
    """A fully tab-wired ``MainWindow`` built the way ``app.main`` builds it.

    Yields ``(window, tab_titles, tabs_by_title)``. Shared by
    ``test_app_deck_builder_tab`` and the video/audio(book)/reading tab-order
    tests that assert against the real tab layout.
    """
    window, titles, tabs = _build_tabs(patch_heavy_init, test_config)
    qtbot.addWidget(window)
    yield window, titles, tabs
    window.deleteLater()
