"""Tests for the Text size (UI font scale) control on the ThemesPanel.

Covers the slider/label sync, the apply-on-release vs apply-on-keyboard
behavior, and the settings_tab forwarding slot that folds the scale into the
config. Theme font scale is reset to 1.0 in teardown so state does not leak
into other tests sharing the Theme singleton.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication

from anki_miner.config import create_default_config
from anki_miner.gui.resources.styles.theme import REQUIRED_COLOR_KEYS, Theme
from anki_miner.gui.widgets.panels.themes_panel import ThemesPanel
from anki_miner.gui.widgets.settings_tab import SettingsTab


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _reset_app_font_scale_state():
    """Reset the Theme scale AND clear any enlarged stylesheet the apply path
    left on the shared QApplication.

    The font-scale apply path (slider release / keyboard step) runs
    ``Theme.apply_to_app`` → ``app.setStyleSheet(<enlarged QSS>)`` on the
    process-wide QApplication. Resetting only ``set_font_scale(1.0)`` leaves the
    enlarged stylesheet applied, distorting widget font metrics / row heights in
    later tests. Clear it here so every test in this module restores a clean
    slate even if the body raises.
    """
    yield
    Theme.set_font_scale(1.0)
    app = QApplication.instance()
    if isinstance(app, QApplication):
        app.setStyleSheet("")


def _theme_dict(name: str, **overrides) -> dict:
    data: dict = {
        "name": name,
        "colors": dict.fromkeys(REQUIRED_COLOR_KEYS, "#000000"),
    }
    data.update(overrides)
    return data


@pytest.fixture
def themes_dir(tmp_path: Path) -> Path:
    d = tmp_path / "themes"
    d.mkdir()
    (d / "light.json").write_text(json.dumps(_theme_dict("Light")))
    (d / "dark.json").write_text(json.dumps(_theme_dict("Dark")))
    return d


@pytest.fixture
def panel(qapp, themes_dir: Path):
    Theme.initialize(active="light", favorites=("light", "dark"), shipped_dir=themes_dir)
    p = ThemesPanel(themes_dir)
    yield p
    # Reset font scale so it cannot leak into other tests on the singleton.
    Theme.set_font_scale(1.0)


class TestApplyPath:
    def test_release_applies_and_emits(self, panel: ThemesPanel) -> None:
        captured: list[float] = []
        panel.font_scale_changed.connect(captured.append)

        # Simulate a mouse drag: press, drag to 150 (label-only, no apply),
        # release (single apply).
        panel.font_scale_slider.sliderPressed.emit()
        panel.font_scale_slider.setValue(150)
        assert panel.font_scale_label.text() == "150%"
        # Mid-drag: Theme scale is untouched, nothing emitted yet.
        assert Theme.get_font_scale() == 1.0
        assert captured == []

        panel.font_scale_slider.sliderReleased.emit()
        assert Theme.get_font_scale() == 1.5
        assert captured == [1.5]

    def test_keyboard_style_change_applies_immediately(self, panel: ThemesPanel) -> None:
        # No press → a value change (e.g. keyboard arrow / page step) applies
        # immediately without waiting for a release.
        captured: list[float] = []
        panel.font_scale_changed.connect(captured.append)

        panel.font_scale_slider.setValue(180)
        assert Theme.get_font_scale() == pytest.approx(1.8)
        assert panel.font_scale_label.text() == "180%"
        assert captured == [pytest.approx(1.8)]


class TestSyncFromTheme:
    def test_sync_sets_slider_and_label_without_emitting(self, panel: ThemesPanel) -> None:
        Theme.set_font_scale(2.0)
        captured: list[float] = []
        panel.font_scale_changed.connect(captured.append)

        panel._sync_font_scale_slider()

        assert panel.font_scale_slider.value() == 200
        assert panel.font_scale_label.text() == "200%"
        # Signals are blocked during sync — no spurious apply/emit.
        assert captured == []
        # And Theme scale is unchanged (sync reads, never writes).
        assert Theme.get_font_scale() == 2.0

    def test_initial_value_reflects_theme(self, qapp, themes_dir: Path) -> None:
        Theme.initialize(active="light", favorites=("light",), shipped_dir=themes_dir)
        Theme.set_font_scale(1.5)
        try:
            p = ThemesPanel(themes_dir)
            assert p.font_scale_slider.value() == 150
            assert p.font_scale_label.text() == "150%"
        finally:
            Theme.set_font_scale(1.0)


class TestLabelTracksDuringDrag:
    def test_label_tracks_apply_deferred(self, panel: ThemesPanel) -> None:
        panel.font_scale_slider.sliderPressed.emit()
        panel.font_scale_slider.setValue(120)
        assert panel.font_scale_label.text() == "120%"
        # Apply is deferred until release: Theme scale stays at baseline.
        assert Theme.get_font_scale() == 1.0
        panel.font_scale_slider.setValue(170)
        assert panel.font_scale_label.text() == "170%"
        assert Theme.get_font_scale() == 1.0


class TestSettingsTabForwarding:
    def test_on_font_scale_changed_updates_config_and_emits(self, qapp, themes_dir: Path) -> None:
        Theme.initialize(active="light", favorites=("light",), shipped_dir=themes_dir)
        config = create_default_config()
        tab = SettingsTab(config)
        try:
            captured: list[object] = []
            tab.config_changed.connect(captured.append)

            tab._on_font_scale_changed(1.5)

            assert tab.config.ui_font_scale == 1.5
            assert len(captured) == 1
            new_config = captured[0]
            assert isinstance(new_config, type(config))
            assert new_config.ui_font_scale == 1.5
        finally:
            Theme.set_font_scale(1.0)

    def test_panel_signal_flows_into_config(self, qapp, themes_dir: Path) -> None:
        # End-to-end: panel apply → settings_tab slot → config mutated.
        Theme.initialize(active="light", favorites=("light",), shipped_dir=themes_dir)
        config = replace(create_default_config(), themes_root=themes_dir)
        tab = SettingsTab(config)
        try:
            tab.themes_panel.font_scale_slider.setValue(160)  # keyboard-style apply
            assert tab.config.ui_font_scale == pytest.approx(1.6)
        finally:
            Theme.set_font_scale(1.0)
