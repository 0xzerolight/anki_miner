"""Tests for app.py wiring the alass install to a post-install refresh.

Regression: the in-app "Download alass" button installed the binary but only
refreshed the Settings panel label — it did not drop the resolver's cached
PATH-miss or re-propagate config, so the (non-Settings) Retime tab stayed
disabled until a Settings save or restart. The production wiring lives in
``anki_miner.gui.app._connect_alass_download``; these tests call that real
helper so the download→retime path cannot silently regress.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def wired(monkeypatch, patch_heavy_init, test_config, qtbot):
    """MainWindow + SettingsTab joined by the production wiring helper.

    ``start_alass_download`` is replaced with a recorder that captures the
    ``on_finished`` callback so the test can fire it without a real worker.
    """
    patch_heavy_init(test_config)
    # notify_alass_download_finished() kicks off the panel's off-thread
    # re-probe, which calls alass_available -> alass_resolver._resolve and
    # RE-POPULATES the global _CACHE on a background thread. That write races
    # the synchronous _clear_cache() + `assert _CACHE == {}` below: locally the
    # probe lands after the assert (green), on CI it lands before (red). Neuter
    # the re-probe so these tests observe only the synchronous handler they
    # actually verify; the panel's own async refresh has its own tests.
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

    def _fake_start(bin_root, status_cb, on_finished):
        captured["on_finished"] = on_finished

    monkeypatch.setattr(window.background_tasks, "start_alass_download", _fake_start)
    app_module._connect_alass_download(window, settings_tab)

    refreshed: list = []
    window.config_refreshed.connect(lambda cfg: refreshed.append(cfg))

    yield window, settings_tab, captured, refreshed
    window.deleteLater()


class TestAlassDownloadWiring:
    def test_successful_install_clears_cache_and_refreshes(self, monkeypatch, wired):
        from anki_miner.utils import alass_resolver

        window, settings_tab, captured, refreshed = wired
        settings_tab.alass_download_requested.emit()
        assert "on_finished" in captured  # button → background install requested

        # Seed a stale cache entry; the successful install must drop it.
        alass_resolver._CACHE[("alass", None, None, False, None)] = "alass"
        captured["on_finished"](True, "Installed")

        assert alass_resolver._CACHE == {}
        assert refreshed == [window.get_config()]  # Retime tab re-evaluates

    def test_failed_install_does_not_refresh(self, wired):
        from anki_miner.utils import alass_resolver

        _window, settings_tab, captured, refreshed = wired
        settings_tab.alass_download_requested.emit()

        alass_resolver._CACHE[("alass", None, None, False, None)] = "alass"
        captured["on_finished"](False, "Download failed")

        # A failed install leaves the cache untouched and emits no refresh.
        assert alass_resolver._CACHE.get(("alass", None, None, False, None)) == "alass"
        assert refreshed == []
        alass_resolver._clear_cache()
