"""Tests for :class:`MainWindow.update_config` config_refreshed propagation (T-13).

A non-Settings config mutation (theme cycle, skip-update, first-run flag) must
emit ``config_refreshed`` so ``SettingsTab.config`` stays in sync; otherwise the
next Settings Save resurrects the pre-change value. A mutation that came FROM the
Settings save path must NOT re-emit (SettingsTab + tabs already saw it via
``config_changed`` directly), to avoid a redundant mid-save reload.

Builds a real ``MainWindow`` with heavy startup side effects patched out, like
``test_main_window_menu``.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from PyQt6.QtWidgets import QApplication

from anki_miner.config import AnkiMinerConfig

# QApplication required for any Qt widget test.
_app = QApplication.instance() or QApplication([])


def _patch_heavy_init(monkeypatch, test_config: AnkiMinerConfig) -> None:
    """Replace config persistence, validation service, and auto-check calls."""
    from anki_miner.gui import main_window as mw_module

    monkeypatch.setattr(mw_module.GUIConfigManager, "load_config", lambda: test_config)
    monkeypatch.setattr(mw_module.GUIConfigManager, "save_config", lambda cfg: None)
    monkeypatch.setattr(mw_module.ValidationService, "__init__", lambda self, *a, **kw: None)
    monkeypatch.setattr(mw_module.MainWindow, "_run_validation", lambda self: None)
    monkeypatch.setattr(mw_module.MainWindow, "_check_for_updates", lambda self: None)
    monkeypatch.setattr(mw_module.MainWindow, "_maybe_create_shortcut_on_first_run", lambda self: None)


@pytest.fixture
def main_window(monkeypatch, test_config):
    """Build a MainWindow without side-effect-heavy startup behaviour."""
    _patch_heavy_init(monkeypatch, test_config)
    from anki_miner.gui.main_window import MainWindow

    window = MainWindow()
    yield window
    window.deleteLater()


# --- T-13: config_refreshed propagation -----------------------------------


def test_update_config_emits_config_refreshed(main_window):
    """A plain update_config (non-Settings) must emit config_refreshed."""
    received: list[AnkiMinerConfig] = []
    main_window.config_refreshed.connect(received.append)

    new_config = replace(main_window.config, theme="dark")
    main_window.update_config(new_config)

    assert received == [new_config]
    assert main_window.config is new_config


def test_update_config_from_settings_does_not_emit(main_window):
    """A Settings-originated save must NOT re-emit config_refreshed.

    SettingsTab + tabs already received the new config via config_changed, so
    re-emitting would trigger a redundant mid-save SettingsTab._load_config().
    """
    received: list[AnkiMinerConfig] = []
    main_window.config_refreshed.connect(received.append)

    new_config = replace(main_window.config, theme="dark")
    main_window.update_config(new_config, from_settings=True)

    assert received == []
    assert main_window.config is new_config


def test_cycle_theme_propagates_to_config_refreshed(main_window, monkeypatch):
    """Ctrl+T theme cycle must propagate via config_refreshed.

    Pins the real bug: SettingsTab.config goes stale after a theme cycle, so
    the next Save writes the old theme back.
    """
    from anki_miner.gui.resources.styles import theme as theme_module

    # Force the cycle to produce a deterministic, different theme.
    monkeypatch.setattr(theme_module.Theme, "cycle_theme", staticmethod(lambda: "dark"))
    main_window.config = replace(main_window.config, theme="light")

    received: list[AnkiMinerConfig] = []
    main_window.config_refreshed.connect(received.append)

    main_window._cycle_theme()

    assert len(received) == 1
    assert received[0].theme == "dark"


def test_skip_update_propagates_to_config_refreshed(main_window):
    """Skip-this-version must propagate so SettingsTab keeps the skipped version."""
    received: list[AnkiMinerConfig] = []
    main_window.config_refreshed.connect(received.append)

    main_window._on_skip_update_requested("9.9.9")

    assert len(received) == 1
    assert received[0].skipped_update_version == "9.9.9"


def test_settings_save_then_unrelated_keeps_theme(main_window):
    """End-to-end: theme cycle then a Settings save of an unrelated field.

    Simulates the real flow — _cycle_theme stores the new theme AND notifies
    SettingsTab (via config_refreshed). A later Settings save (from_settings)
    that carries the up-to-date theme must persist it, not revert.
    """
    saved: list[AnkiMinerConfig] = []

    class _FakeSettings:
        """Stand-in SettingsTab that mirrors config from config_refreshed."""

        def __init__(self, cfg: AnkiMinerConfig) -> None:
            self.config = cfg

        def update_config(self, cfg: AnkiMinerConfig) -> None:
            self.config = cfg

    main_window.config = replace(main_window.config, theme="light")
    fake_settings = _FakeSettings(main_window.config)
    main_window.config_refreshed.connect(fake_settings.update_config)

    # User cycles theme via Ctrl+T equivalent.
    main_window.update_config(replace(main_window.config, theme="dark"))

    # SettingsTab now sees the new theme (no stale revert).
    assert fake_settings.config.theme == "dark"

    # SettingsTab saves an unrelated field, carrying the current theme forward.
    settings_save = replace(fake_settings.config, anki_deck_name="other")
    main_window.update_config(settings_save, from_settings=True)
    saved.append(main_window.config)

    assert saved[-1].theme == "dark"
    assert saved[-1].anki_deck_name == "other"
