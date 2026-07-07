"""Tests for the Zoom (whole-UI scale) control on the UISettingsPanel.

Zoom is restart-to-apply (injected as QT_SCALE_FACTOR before QApplication is
built), so unlike Text size there is no live Theme/restyle path — selecting a
preset only persists ``ui_zoom`` and reveals a restart note. The combo is
seeded from the passed-in ``ui_zoom`` rather than Theme state.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from anki_miner.config import create_default_config
from anki_miner.gui.resources.styles.theme import REQUIRED_COLOR_KEYS, Theme
from anki_miner.gui.widgets.panels.ui_settings_panel import ZOOM_PRESETS, UISettingsPanel
from anki_miner.gui.widgets.settings_tab import SettingsTab


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
    p = UISettingsPanel(themes_dir)
    qtbot.addWidget(p)
    return p


def _index_for_percent(panel: UISettingsPanel, percent: int) -> int:
    return panel.zoom_combo.findData(percent)


class TestComboPopulation:
    def test_combo_exists_with_object_name(self, panel: UISettingsPanel) -> None:
        assert panel.zoom_combo.objectName() == "zoomCombo"

    def test_combo_has_exactly_the_presets(self, panel: UISettingsPanel) -> None:
        combo = panel.zoom_combo
        assert combo.count() == len(ZOOM_PRESETS)
        for i, p in enumerate(ZOOM_PRESETS):
            assert combo.itemText(i) == f"{p}%"
            assert combo.itemData(i) == p

    def test_presets_within_clamp_range(self) -> None:
        # ui_zoom is clamped to [0.5, 2.0] in AnkiMinerConfig.__post_init__.
        assert all(50 <= p <= 200 for p in ZOOM_PRESETS)

    def test_restart_note_hidden_initially(self, panel: UISettingsPanel) -> None:
        assert panel.zoom_restart_note.isHidden() is True


class TestApplyPath:
    @pytest.mark.parametrize(
        ("percent", "expected_zoom"),
        [(75, 0.75), (100, 1.0), (125, 1.25), (200, 2.0)],
    )
    def test_selecting_preset_emits_and_reveals_note(
        self, panel: UISettingsPanel, percent: int, expected_zoom: float
    ) -> None:
        captured: list[float] = []
        panel.zoom_changed.connect(captured.append)

        idx = _index_for_percent(panel, percent)
        panel._on_zoom_selected(idx)

        assert captured == [pytest.approx(expected_zoom)]
        assert panel.zoom_restart_note.isHidden() is False
        assert panel._ui_zoom == pytest.approx(expected_zoom)

    def test_activated_signal_drives_apply(self, panel: UISettingsPanel) -> None:
        captured: list[float] = []
        panel.zoom_changed.connect(captured.append)

        idx = _index_for_percent(panel, 150)
        panel.zoom_combo.activated.emit(idx)

        assert captured == [pytest.approx(1.5)]
        assert panel.zoom_restart_note.isHidden() is False


class TestSyncFromConfig:
    def test_sync_selects_index_without_emitting(self, qapp, qtbot, themes_dir: Path) -> None:
        Theme.initialize(active="light", favorites=("light",), shipped_dir=themes_dir)
        captured: list[float] = []

        p = UISettingsPanel(themes_dir, ui_zoom=2.0)
        qtbot.addWidget(p)
        p.zoom_changed.connect(captured.append)

        # Constructor sync seeded the combo from ui_zoom and did not emit nor
        # reveal the restart note.
        assert p.zoom_combo.currentData() == 200
        assert captured == []
        assert p.zoom_restart_note.isHidden() is True

    def test_legacy_non_preset_zoom_snaps_to_nearest(self, qapp, qtbot, themes_dir: Path) -> None:
        # 1.30 (130%) is not a preset; the display snaps to the nearest (125%).
        Theme.initialize(active="light", favorites=("light",), shipped_dir=themes_dir)
        p = UISettingsPanel(themes_dir, ui_zoom=1.30)
        qtbot.addWidget(p)
        assert p.zoom_combo.currentData() == 125

    def test_default_zoom_selects_100(self, panel: UISettingsPanel) -> None:
        # Default ui_zoom (1.0) seeds the 100% entry.
        assert panel.zoom_combo.currentData() == 100


class TestSettingsTabForwarding:
    def test_on_zoom_changed_updates_config_and_emits(self, qapp, qtbot, themes_dir: Path) -> None:
        Theme.initialize(active="light", favorites=("light",), shipped_dir=themes_dir)
        config = create_default_config()
        tab = SettingsTab(config)
        qtbot.addWidget(tab)

        captured: list[object] = []
        tab.config_changed.connect(captured.append)

        tab._on_zoom_changed(1.5)

        assert tab.config.ui_zoom == 1.5
        assert len(captured) == 1
        new_config = captured[0]
        assert isinstance(new_config, type(config))
        assert new_config.ui_zoom == 1.5

    def test_panel_signal_flows_into_config(self, qapp, qtbot, themes_dir: Path) -> None:
        # End-to-end: panel selection → settings_tab slot → config mutated.
        Theme.initialize(active="light", favorites=("light",), shipped_dir=themes_dir)
        config = replace(create_default_config(), themes_root=themes_dir)
        tab = SettingsTab(config)
        qtbot.addWidget(tab)

        combo = tab.ui_panel.zoom_combo
        idx = combo.findData(150)
        tab.ui_panel._on_zoom_selected(idx)
        assert tab.config.ui_zoom == pytest.approx(combo.itemData(idx) / 100.0)

    def test_panel_seeded_from_config_zoom(self, qapp, qtbot, themes_dir: Path) -> None:
        Theme.initialize(active="light", favorites=("light",), shipped_dir=themes_dir)
        config = replace(create_default_config(), themes_root=themes_dir, ui_zoom=1.75)
        tab = SettingsTab(config)
        qtbot.addWidget(tab)
        assert tab.ui_panel.zoom_combo.currentData() == 175
