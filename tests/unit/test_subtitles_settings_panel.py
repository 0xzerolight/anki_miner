"""Tests for SubtitlesSettingsPanel — load_from_config/contribute round-trip."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.panels.subtitles_settings_panel import SubtitlesSettingsPanel

# ---------------------------------------------------------------------------
# Panel construction
# ---------------------------------------------------------------------------


def test_panel_constructs(qtbot):
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    assert panel is not None


def test_panel_has_alass_selector(qtbot):
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    assert panel.alass_selector is not None


# ---------------------------------------------------------------------------
# load_from_config
# ---------------------------------------------------------------------------


def test_load_from_config_populates_selector_from_path(qtbot, tmp_path):
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    alass_path = tmp_path / "alass"
    config = AnkiMinerConfig(alass_location=alass_path)
    panel.load_from_config(config)
    assert panel.alass_selector.get_path() == str(alass_path)


def test_load_from_config_with_none_clears_selector(qtbot):
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    config = AnkiMinerConfig(alass_location=None)
    panel.load_from_config(config)
    assert panel.alass_selector.get_path() == ""


def test_load_from_config_replaces_previous_value(qtbot, tmp_path):
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    first_path = tmp_path / "alass_v1"
    second_path = tmp_path / "alass_v2"

    panel.load_from_config(AnkiMinerConfig(alass_location=first_path))
    assert panel.alass_selector.get_path() == str(first_path)

    panel.load_from_config(AnkiMinerConfig(alass_location=second_path))
    assert panel.alass_selector.get_path() == str(second_path)


# ---------------------------------------------------------------------------
# contribute
# ---------------------------------------------------------------------------


def test_contribute_with_path_set_returns_config_with_alass_location(qtbot, tmp_path):
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    alass_path = tmp_path / "alass"
    panel.alass_selector.set_path(str(alass_path))
    config = AnkiMinerConfig()
    new_config = panel.contribute(config)
    assert new_config.alass_location == alass_path


def test_contribute_with_empty_selector_returns_none(qtbot):
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    panel.alass_selector.set_path("")
    config = AnkiMinerConfig()
    new_config = panel.contribute(config)
    assert new_config.alass_location is None


def test_contribute_does_not_mutate_original_config(qtbot, tmp_path):
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    alass_path = tmp_path / "alass"
    panel.alass_selector.set_path(str(alass_path))
    config = AnkiMinerConfig(alass_location=None)
    panel.contribute(config)
    # Original config unchanged (frozen dataclass)
    assert config.alass_location is None


def test_contribute_whitespace_only_is_treated_as_none(qtbot):
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    panel.alass_selector.set_path("   ")
    config = AnkiMinerConfig()
    new_config = panel.contribute(config)
    assert new_config.alass_location is None


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_round_trip_with_path(qtbot, tmp_path):
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    alass_path = tmp_path / "alass"
    config = AnkiMinerConfig(alass_location=alass_path)
    panel.load_from_config(config)
    result = panel.contribute(config)
    assert result.alass_location == alass_path


def test_round_trip_with_none(qtbot):
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    config = AnkiMinerConfig(alass_location=None)
    panel.load_from_config(config)
    result = panel.contribute(config)
    assert result.alass_location is None
