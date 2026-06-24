"""Tests for AsrSettingsPanel — load_from_config/contribute round-trip and signals."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.panels.asr_settings_panel import AsrSettingsPanel

# ---------------------------------------------------------------------------
# Panel construction
# ---------------------------------------------------------------------------


def test_panel_constructs(qtbot):
    panel = AsrSettingsPanel()
    qtbot.addWidget(panel)
    assert panel is not None


def test_panel_has_model_combo(qtbot):
    panel = AsrSettingsPanel()
    qtbot.addWidget(panel)
    items = [panel.model_combo.itemText(i) for i in range(panel.model_combo.count())]
    assert "large-v3" in items
    assert "small" in items


def test_panel_has_download_button(qtbot):
    panel = AsrSettingsPanel()
    qtbot.addWidget(panel)
    assert panel.download_model_button is not None


def test_panel_has_status_label(qtbot):
    panel = AsrSettingsPanel()
    qtbot.addWidget(panel)
    assert panel.model_status_label is not None


# ---------------------------------------------------------------------------
# load_from_config / contribute round-trip
# ---------------------------------------------------------------------------


def test_load_from_config_sets_large_v3(qtbot, tmp_path):
    panel = AsrSettingsPanel()
    qtbot.addWidget(panel)
    config = AnkiMinerConfig(asr_model="large-v3", asr_models_root=tmp_path)
    panel.load_from_config(config)
    assert panel.model_combo.currentText() == "large-v3"


def test_load_from_config_sets_small(qtbot, tmp_path):
    panel = AsrSettingsPanel()
    qtbot.addWidget(panel)
    config = AnkiMinerConfig(asr_model="small", asr_models_root=tmp_path)
    panel.load_from_config(config)
    assert panel.model_combo.currentText() == "small"


def test_contribute_returns_new_config_with_asr_model(qtbot, tmp_path):
    panel = AsrSettingsPanel()
    qtbot.addWidget(panel)
    config = AnkiMinerConfig(asr_model="large-v3", asr_models_root=tmp_path)
    panel.load_from_config(config)

    # Switch to small
    panel.model_combo.setCurrentText("small")
    new_config = panel.contribute(config)

    assert new_config.asr_model == "small"
    # Original config unchanged (frozen)
    assert config.asr_model == "large-v3"


def test_contribute_round_trip_large_v3(qtbot, tmp_path):
    panel = AsrSettingsPanel()
    qtbot.addWidget(panel)
    config = AnkiMinerConfig(asr_model="large-v3", asr_models_root=tmp_path)
    panel.load_from_config(config)
    result = panel.contribute(config)
    assert result.asr_model == "large-v3"


def test_contribute_round_trip_small(qtbot, tmp_path):
    panel = AsrSettingsPanel()
    qtbot.addWidget(panel)
    config = AnkiMinerConfig(asr_model="small", asr_models_root=tmp_path)
    panel.load_from_config(config)
    result = panel.contribute(config)
    assert result.asr_model == "small"


# ---------------------------------------------------------------------------
# Download signal
# ---------------------------------------------------------------------------


def test_download_button_emits_signal(qtbot, tmp_path):
    """Clicking Download emits asr_download_requested."""
    panel = AsrSettingsPanel()
    qtbot.addWidget(panel)
    config = AnkiMinerConfig(asr_model="large-v3", asr_models_root=tmp_path)
    panel.load_from_config(config)

    received: list[str] = []
    panel.asr_download_requested.connect(received.append)

    panel.download_model_button.click()

    assert len(received) == 1
    assert received[0] == "large-v3"


def test_download_button_emits_selected_model(qtbot, tmp_path):
    """Signal carries the currently selected model name."""
    panel = AsrSettingsPanel()
    qtbot.addWidget(panel)
    config = AnkiMinerConfig(asr_model="large-v3", asr_models_root=tmp_path)
    panel.load_from_config(config)

    panel.model_combo.setCurrentText("small")

    received: list[str] = []
    panel.asr_download_requested.connect(received.append)

    panel.download_model_button.click()

    assert received == ["small"]


# ---------------------------------------------------------------------------
# Status label
# ---------------------------------------------------------------------------


def test_set_model_status_updates_label(qtbot):
    panel = AsrSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_model_status("Downloading…")
    assert panel.model_status_label.text() == "Downloading…"


def test_load_from_config_reflects_download_state_when_downloaded(qtbot, tmp_path, monkeypatch):
    """Status label says 'Downloaded' when model is present."""
    monkeypatch.setattr(
        "anki_miner.gui.widgets.panels.asr_settings_panel.model_manager.is_downloaded",
        lambda name, root: True,
    )
    panel = AsrSettingsPanel()
    qtbot.addWidget(panel)
    config = AnkiMinerConfig(asr_model="large-v3", asr_models_root=tmp_path)
    panel.load_from_config(config)
    assert "downloaded" in panel.model_status_label.text().lower()


def test_load_from_config_reflects_download_state_when_not_downloaded(qtbot, tmp_path, monkeypatch):
    """Status label says 'Not downloaded' when model is absent."""
    monkeypatch.setattr(
        "anki_miner.gui.widgets.panels.asr_settings_panel.model_manager.is_downloaded",
        lambda name, root: False,
    )
    panel = AsrSettingsPanel()
    qtbot.addWidget(panel)
    config = AnkiMinerConfig(asr_model="large-v3", asr_models_root=tmp_path)
    panel.load_from_config(config)
    assert "not downloaded" in panel.model_status_label.text().lower()


def test_load_from_config_handles_is_downloaded_error(qtbot, tmp_path, monkeypatch):
    """is_downloaded raising (NotImplementedError) is caught gracefully."""
    monkeypatch.setattr(
        "anki_miner.gui.widgets.panels.asr_settings_panel.model_manager.is_downloaded",
        lambda name, root: (_ for _ in ()).throw(NotImplementedError()),
    )
    panel = AsrSettingsPanel()
    qtbot.addWidget(panel)
    config = AnkiMinerConfig(asr_model="large-v3", asr_models_root=tmp_path)
    # Should not raise
    panel.load_from_config(config)
