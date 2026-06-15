"""Tests for the Text size (UI font scale) control on the ThemesPanel.

Covers the combo population, the apply-on-selection path, the read-only sync
from Theme state, nearest-preset snapping for legacy custom scales, and the
settings_tab forwarding slot that folds the scale into the config. Theme font
scale is reset to 1.0 in teardown so state does not leak into other tests
sharing the Theme singleton.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication

from anki_miner.config import create_default_config
from anki_miner.gui.resources.styles.theme import FONT_SCALE_MAX, FONT_SCALE_MIN, REQUIRED_COLOR_KEYS, Theme
from anki_miner.gui.widgets.panels.themes_panel import FONT_SCALE_PRESETS, ThemesPanel
from anki_miner.gui.widgets.settings_tab import SettingsTab


@pytest.fixture(autouse=True)
def _reset_app_font_scale_state():
    """Reset the Theme scale AND clear any enlarged stylesheet the apply path
    left on the shared QApplication.

    The font-scale apply path runs ``Theme.apply_to_app`` →
    ``app.setStyleSheet(<enlarged QSS>)`` on the process-wide QApplication.
    Resetting only ``set_font_scale(1.0)`` leaves the enlarged stylesheet
    applied, distorting widget font metrics / row heights in later tests.
    Clear it here so every test in this module restores a clean slate even if
    the body raises.
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
def panel(qapp, qtbot, themes_dir: Path):
    Theme.initialize(active="light", favorites=("light", "dark"), shipped_dir=themes_dir)
    p = ThemesPanel(themes_dir)
    qtbot.addWidget(p)
    yield p
    # Reset font scale so it cannot leak into other tests on the singleton.
    Theme.set_font_scale(1.0)


def _index_for_percent(panel: ThemesPanel, percent: int) -> int:
    return panel.font_scale_combo.findData(percent)


class TestComboPopulation:
    def test_combo_exists_with_object_name(self, panel: ThemesPanel) -> None:
        assert panel.font_scale_combo.objectName() == "fontScaleCombo"

    def test_combo_has_exactly_the_presets(self, panel: ThemesPanel) -> None:
        combo = panel.font_scale_combo
        assert combo.count() == len(FONT_SCALE_PRESETS)
        for i, p in enumerate(FONT_SCALE_PRESETS):
            assert combo.itemText(i) == f"{p}%"
            assert combo.itemData(i) == p


def test_presets_within_clamp_range() -> None:
    assert all(FONT_SCALE_MIN * 100 <= p <= FONT_SCALE_MAX * 100 for p in FONT_SCALE_PRESETS)


class TestApplyPath:
    @pytest.mark.parametrize(
        ("percent", "expected_scale"),
        [(50, 0.5), (100, 1.0), (125, 1.25), (200, 2.0)],
    )
    def test_selecting_preset_applies_and_emits(self, panel: ThemesPanel, percent: int, expected_scale: float) -> None:
        captured: list[float] = []
        panel.font_scale_changed.connect(captured.append)

        idx = _index_for_percent(panel, percent)
        panel.font_scale_combo.setCurrentIndex(idx)
        panel._on_font_scale_selected(idx)

        assert Theme.get_font_scale() == pytest.approx(expected_scale)
        assert captured == [pytest.approx(expected_scale)]

    def test_activated_signal_drives_apply(self, panel: ThemesPanel) -> None:
        captured: list[float] = []
        panel.font_scale_changed.connect(captured.append)

        idx = _index_for_percent(panel, 150)
        panel.font_scale_combo.activated.emit(idx)

        assert Theme.get_font_scale() == pytest.approx(1.5)
        assert captured == [pytest.approx(1.5)]


class TestSyncFromTheme:
    def test_sync_selects_index_without_emitting(self, panel: ThemesPanel) -> None:
        Theme.set_font_scale(2.0)
        captured: list[float] = []
        panel.font_scale_changed.connect(captured.append)

        panel._sync_font_scale_combo()

        assert panel.font_scale_combo.currentData() == 200
        # Signals are blocked during sync — no spurious apply/emit.
        assert captured == []
        # And Theme scale is unchanged (sync reads, never writes).
        assert Theme.get_font_scale() == 2.0

    def test_legacy_non_preset_scale_snaps_to_nearest(self, panel: ThemesPanel) -> None:
        # A legacy custom scale of 1.3 (130%) is not a preset; the display
        # snaps to the nearest preset (125%) and must not emit.
        Theme.set_font_scale(1.3)
        captured: list[float] = []
        panel.font_scale_changed.connect(captured.append)

        panel._sync_font_scale_combo()

        assert panel.font_scale_combo.currentData() == 125
        assert captured == []
        assert Theme.get_font_scale() == 1.3

    def test_initial_value_reflects_theme(self, qapp, qtbot, themes_dir: Path) -> None:
        Theme.initialize(active="light", favorites=("light",), shipped_dir=themes_dir)
        Theme.set_font_scale(1.5)
        try:
            p = ThemesPanel(themes_dir)
            qtbot.addWidget(p)
            assert p.font_scale_combo.currentData() == 150
        finally:
            Theme.set_font_scale(1.0)


class TestSettingsTabForwarding:
    def test_on_font_scale_changed_updates_config_and_emits(self, qapp, qtbot, themes_dir: Path) -> None:
        Theme.initialize(active="light", favorites=("light",), shipped_dir=themes_dir)
        config = create_default_config()
        tab = SettingsTab(config)
        qtbot.addWidget(tab)
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

    def test_panel_signal_flows_into_config(self, qapp, qtbot, themes_dir: Path) -> None:
        # End-to-end: panel selection → settings_tab slot → config mutated.
        Theme.initialize(active="light", favorites=("light",), shipped_dir=themes_dir)
        config = replace(create_default_config(), themes_root=themes_dir)
        tab = SettingsTab(config)
        qtbot.addWidget(tab)
        try:
            combo = tab.themes_panel.font_scale_combo
            idx = combo.findData(150)
            combo.setCurrentIndex(idx)
            tab.themes_panel._on_font_scale_selected(idx)
            assert tab.config.ui_font_scale == pytest.approx(combo.itemData(idx) / 100.0)
        finally:
            Theme.set_font_scale(1.0)
