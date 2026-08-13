"""Tests for the Text size (UI font scale) control on the UISettingsPanel.

Text size is restart-to-apply (decision D39b-A): selecting a preset persists the
choice and reveals a note with *Restart now* / *Later*, and the running process
keeps the scale it booted with. These tests pin exactly that — that nothing is
restyled live, that the combo tracks the pending config value rather than the
running ``Theme``, and that the note appears, dismisses and returns correctly.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from anki_miner.config import create_default_config
from anki_miner.gui import restart
from anki_miner.gui.resources.styles.theme import FONT_SCALE_MAX, FONT_SCALE_MIN, REQUIRED_COLOR_KEYS, Theme
from anki_miner.gui.widgets.panels.ui_settings_panel import FONT_SCALE_PRESETS, UISettingsPanel
from anki_miner.gui.widgets.settings_tab import SettingsTab


@pytest.fixture(autouse=True)
def _reset_restart_intent():
    yield
    restart.clear_restart_request()


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
    return panel.font_scale_combo.findData(percent)


class TestComboPopulation:
    def test_combo_exists_with_object_name(self, panel: UISettingsPanel) -> None:
        assert panel.font_scale_combo.objectName() == "fontScaleCombo"

    def test_combo_has_exactly_the_presets(self, panel: UISettingsPanel) -> None:
        combo = panel.font_scale_combo
        assert combo.count() == len(FONT_SCALE_PRESETS)
        for i, p in enumerate(FONT_SCALE_PRESETS):
            assert combo.itemText(i) == f"{p}%"
            assert combo.itemData(i) == p


def test_presets_within_clamp_range() -> None:
    assert all(FONT_SCALE_MIN * 100 <= p <= FONT_SCALE_MAX * 100 for p in FONT_SCALE_PRESETS)


class TestRestartToApply:
    @pytest.mark.parametrize(
        ("percent", "expected_scale"),
        [(50, 0.5), (125, 1.25), (200, 2.0)],
    )
    def test_selecting_a_preset_persists_without_restyling(
        self, panel: UISettingsPanel, percent: int, expected_scale: float
    ) -> None:
        captured: list[float] = []
        panel.font_scale_changed.connect(captured.append)

        idx = _index_for_percent(panel, percent)
        panel.font_scale_combo.setCurrentIndex(idx)
        panel._on_font_scale_selected(idx)

        assert captured == [pytest.approx(expected_scale)]
        # The running process is untouched: this is the whole point of D39b-A.
        assert Theme.get_font_scale() == pytest.approx(1.0)
        assert panel.font_scale_restart_row.isVisible() or not panel.isVisible()
        assert panel.font_scale_restart_note.text() == "Restart to apply."

    def test_activated_signal_drives_the_same_path(self, panel: UISettingsPanel) -> None:
        captured: list[float] = []
        panel.font_scale_changed.connect(captured.append)

        panel.font_scale_combo.activated.emit(_index_for_percent(panel, 150))

        assert captured == [pytest.approx(1.5)]
        assert Theme.get_font_scale() == pytest.approx(1.0)

    def test_choosing_the_boot_value_again_hides_the_note(self, panel: UISettingsPanel) -> None:
        panel._on_font_scale_selected(_index_for_percent(panel, 150))
        panel._on_font_scale_selected(_index_for_percent(panel, 100))
        assert not panel.font_scale_restart_row.isVisible()

    def test_later_hides_the_note_without_reverting(self, panel: UISettingsPanel) -> None:
        idx = _index_for_percent(panel, 175)
        panel.font_scale_combo.setCurrentIndex(idx)
        panel._on_font_scale_selected(idx)
        panel._on_restart_later()

        assert not panel.font_scale_restart_row.isVisible()
        # The choice itself survives; only the reminder went away.
        assert panel._ui_font_scale == pytest.approx(1.75)
        assert panel.font_scale_combo.currentData() == 175

    def test_a_new_selection_after_later_shows_the_note_again(self, panel: UISettingsPanel, qtbot) -> None:
        panel.show()
        qtbot.waitExposed(panel)
        panel._on_font_scale_selected(_index_for_percent(panel, 175))
        panel._on_restart_later()
        panel._on_font_scale_selected(_index_for_percent(panel, 125))

        assert panel.font_scale_restart_row.isVisible()


class TestRestartNow:
    def test_unresolvable_executable_neither_closes_nor_launches(self, panel: UISettingsPanel, monkeypatch) -> None:
        monkeypatch.setattr(restart, "resolve_relaunch_target", lambda: None)
        closed: list[bool] = []
        monkeypatch.setattr(type(panel), "window", lambda self: None)

        panel._on_restart_now()

        assert not restart.restart_requested()
        assert closed == []
        banner = panel.issue_banner()
        assert banner is not None and banner.current_issue() is not None

    def test_success_records_intent_and_closes_the_window(
        self, panel: UISettingsPanel, monkeypatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(restart, "resolve_relaunch_target", lambda: tmp_path / "anki_miner_gui")

        class _Window:
            def __init__(self) -> None:
                self.closed = False

            def close(self) -> bool:
                self.closed = True
                return True

        fake = _Window()
        monkeypatch.setattr(type(panel), "window", lambda self: fake)

        panel._on_restart_now()

        assert fake.closed
        assert restart.restart_requested()

    def test_a_refused_close_clears_the_intent(self, panel: UISettingsPanel, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr(restart, "resolve_relaunch_target", lambda: tmp_path / "anki_miner_gui")
        monkeypatch.setattr(type(panel), "window", lambda self: type("W", (), {"close": lambda s: False})())

        panel._on_restart_now()

        assert not restart.restart_requested()

    def test_a_deferred_close_keeps_the_intent(self, panel: UISettingsPanel, monkeypatch, tmp_path: Path) -> None:
        """A worker outliving the join grace must not cancel the relaunch.

        ``MainWindow`` refuses the close event while laggards run, hides itself
        and quits from a poll once the last one exits — so ``close()`` reports
        ``False`` for a shutdown that is still going to happen. Reading that as
        "the user changed their mind" left the app closed and never relaunched,
        exactly when it was busiest.
        """
        monkeypatch.setattr(restart, "resolve_relaunch_target", lambda: tmp_path / "anki_miner_gui")
        deferred = type(
            "W",
            (),
            {"close": lambda s: False, "is_shutting_down": lambda s: True},
        )()
        monkeypatch.setattr(type(panel), "window", lambda self: deferred)

        panel._on_restart_now()

        assert restart.restart_requested()


class TestSyncFromPendingConfig:
    def test_sync_reads_the_pending_value_not_the_running_theme(self, panel: UISettingsPanel) -> None:
        Theme.set_font_scale(2.0)
        try:
            captured: list[float] = []
            panel.font_scale_changed.connect(captured.append)
            panel._ui_font_scale = 1.25
            panel._sync_font_scale_combo()

            assert panel.font_scale_combo.currentData() == 125
            assert captured == []
        finally:
            Theme.set_font_scale(1.0)

    def test_legacy_non_preset_scale_snaps_to_nearest(self, panel: UISettingsPanel) -> None:
        panel._ui_font_scale = 1.3
        panel._sync_font_scale_combo()
        assert panel.font_scale_combo.currentData() == 125

    def test_initial_value_reflects_the_config(self, qapp, qtbot, themes_dir: Path) -> None:
        Theme.initialize(active="light", favorites=("light",), shipped_dir=themes_dir)
        p = UISettingsPanel(themes_dir, ui_font_scale=1.5)
        qtbot.addWidget(p)
        assert p.font_scale_combo.currentData() == 150
        # Constructed with a pending scale that differs from the boot one: the
        # note is the honest thing to show, not a silently different window.
        assert p._boot_font_scale == pytest.approx(1.0)


class TestSettingsTabForwarding:
    def test_on_font_scale_changed_updates_config_and_emits(self, qapp, qtbot, themes_dir: Path) -> None:
        Theme.initialize(active="light", favorites=("light",), shipped_dir=themes_dir)
        config = create_default_config()
        tab = SettingsTab(config)
        qtbot.addWidget(tab)
        captured: list[object] = []
        tab.config_changed.connect(captured.append)

        tab._on_font_scale_changed(1.5)

        assert len(captured) == 1
        new_config = captured[0]
        assert isinstance(new_config, type(config))
        assert new_config.ui_font_scale == 1.5

    def test_panel_signal_flows_into_config(self, qapp, qtbot, themes_dir: Path) -> None:
        Theme.initialize(active="light", favorites=("light",), shipped_dir=themes_dir)
        config = replace(create_default_config(), themes_root=themes_dir)
        tab = SettingsTab(config)
        qtbot.addWidget(tab)
        captured: list[object] = []
        tab.config_changed.connect(captured.append)
        combo = tab.ui_panel.font_scale_combo
        idx = combo.findData(150)
        combo.setCurrentIndex(idx)
        tab.ui_panel._on_font_scale_selected(idx)

        assert len(captured) == 1
        new_config = captured[0]
        assert isinstance(new_config, type(config))
        assert new_config.ui_font_scale == pytest.approx(1.5)
        # The settings tab seeded the panel from the config it was built with.
        assert tab.ui_panel._boot_font_scale == pytest.approx(Theme.get_font_scale())
