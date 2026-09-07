"""Tests for app.py wiring the mokuro install to a post-install refresh.

Clone of ``test_app_alass_download_wiring``: the in-app "Install mokuro"
button must, on success, drop the resolver's cached PATH-miss AND re-propagate
config, so the (non-Settings) Manga OCR tab re-runs its availability guard
instead of staying disabled until a Settings save or restart. The production
wiring lives in ``anki_miner.gui.app._connect_mokuro_install``; these tests
call that real helper.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def wired(monkeypatch, patch_heavy_init, test_config, qtbot):
    """MainWindow + SettingsTab joined by the production wiring helper.

    ``start_mokuro_install`` is replaced with a recorder that captures the
    ``on_finished`` callback so the test can fire it without a real worker.
    """
    patch_heavy_init(test_config)
    # notify_mokuro_install_finished() kicks off the panel's off-thread
    # re-probe, which calls mokuro_available -> mokuro_resolver._resolve and
    # RE-POPULATES the global _CACHE on a background thread. That write races
    # the synchronous _clear_cache() + `assert _CACHE == {}` below. Neuter the
    # re-probe so these tests observe only the synchronous handler they verify;
    # the panel's own async refresh has its own tests.
    from anki_miner.gui.widgets.panels.subtitles_settings_panel import (
        SubtitlesSettingsPanel,
    )

    monkeypatch.setattr(SubtitlesSettingsPanel, "_refresh_state_async", lambda self, *a, **kw: None)

    from anki_miner.gui import app as app_module
    from anki_miner.gui.main_window import MainWindow
    from anki_miner.gui.widgets.settings_tab import SettingsTab

    window = MainWindow()
    qtbot.addWidget(window)
    settings_tab = SettingsTab(window.get_config())
    qtbot.addWidget(settings_tab)

    captured: dict = {}

    def _fake_start(bin_root, uv_root, status_cb, on_finished):
        captured["bin_root"] = bin_root
        captured["uv_root"] = uv_root
        captured["on_finished"] = on_finished

    monkeypatch.setattr(window.background_tasks, "start_mokuro_install", _fake_start)
    app_module._connect_mokuro_install(window, settings_tab)

    refreshed: list = []
    window.config_refreshed.connect(lambda cfg: refreshed.append(cfg))

    yield window, settings_tab, captured, refreshed
    window.deleteLater()


class TestMokuroInstallWiring:
    def test_request_starts_install_with_config_roots(self, wired):
        window, settings_tab, captured, _refreshed = wired
        settings_tab.mokuro_install_requested.emit()

        assert "on_finished" in captured  # button → background install requested
        assert captured["bin_root"] == window.get_config().bin_root
        assert captured["uv_root"] == window.get_config().uv_root

    def test_successful_install_clears_cache_and_refreshes(self, wired):
        from anki_miner.utils import mokuro_resolver

        window, settings_tab, captured, refreshed = wired
        settings_tab.mokuro_install_requested.emit()

        # Seed a stale cache entry; the successful install must drop it.
        mokuro_resolver._CACHE[(None, None)] = "mokuro"
        captured["on_finished"](True, "ok")

        assert mokuro_resolver._CACHE == {}
        assert refreshed == [window.get_config()]  # Manga OCR tab re-evaluates

    def test_failed_install_does_not_refresh(self, wired):
        from anki_miner.utils import mokuro_resolver

        _window, settings_tab, captured, refreshed = wired
        settings_tab.mokuro_install_requested.emit()

        mokuro_resolver._CACHE[(None, None)] = "mokuro"
        captured["on_finished"](False, "boom")

        # A failed install leaves the cache untouched and emits no refresh.
        assert mokuro_resolver._CACHE.get((None, None)) == "mokuro"
        assert refreshed == []
        mokuro_resolver._clear_cache()

    def test_cache_is_cleared_before_the_panel_reprobes(self, monkeypatch, wired):
        """Ordering, not just outcome: the panel's re-probe runs off-thread and
        calls the resolver, so a cached pre-install miss still in place when
        notify fires can settle the label on "Not installed" after a success."""
        from anki_miner.utils import mokuro_resolver

        _window, settings_tab, captured, _refreshed = wired
        seen: list[dict] = []
        monkeypatch.setattr(
            settings_tab.subtitles_panel,
            "notify_mokuro_install_finished",
            lambda: seen.append(dict(mokuro_resolver._CACHE)),
        )
        settings_tab.mokuro_install_requested.emit()

        mokuro_resolver._CACHE[(None, None)] = "mokuro"
        captured["on_finished"](True, "ok")

        assert seen == [{}]

    def test_panel_is_notified_on_failure_too(self, monkeypatch, wired):
        """The in-flight guard must clear whether the install worked or not."""
        _window, settings_tab, captured, _refreshed = wired
        calls: list[None] = []
        monkeypatch.setattr(
            settings_tab.subtitles_panel,
            "notify_mokuro_install_finished",
            lambda: calls.append(None),
        )
        settings_tab.mokuro_install_requested.emit()

        captured["on_finished"](False, "boom")

        assert calls == [None]
