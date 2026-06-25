"""Tests for the merged SubtitlesSettingsPanel.

Covers the alass path override/round-trip, the ASR model dropdown + download
gating, the engine-missing guidance, and the in-app alass download button.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.panels.subtitles_settings_panel import SubtitlesSettingsPanel

_PANEL_MOD = "anki_miner.gui.widgets.panels.subtitles_settings_panel"

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


# ---------------------------------------------------------------------------
# ASR section — model dropdown + contribute
# ---------------------------------------------------------------------------


def test_panel_has_model_combo(qtbot):
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    items = [panel.model_combo.itemText(i) for i in range(panel.model_combo.count())]
    assert "large-v3" in items
    assert "small" in items


def test_contribute_preserves_asr_model_and_alass(qtbot, tmp_path):
    """contribute folds BOTH asr_model and alass_location into the new config."""
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    alass_path = tmp_path / "alass"
    config = AnkiMinerConfig(asr_model="large-v3", alass_location=None)
    panel.load_from_config(config)

    panel.model_combo.setCurrentText("small")
    panel.alass_selector.set_path(str(alass_path))
    new_config = panel.contribute(config)

    assert new_config.asr_model == "small"
    assert new_config.alass_location == alass_path
    # Original frozen config untouched.
    assert config.asr_model == "large-v3"


# ---------------------------------------------------------------------------
# ASR engine-missing guidance + download gating
# ---------------------------------------------------------------------------


def test_engine_unavailable_disables_download_and_shows_guidance(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(f"{_PANEL_MOD}._engine.available", lambda: False)
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(AnkiMinerConfig(asr_models_root=tmp_path))

    assert not panel.download_model_button.isEnabled()
    assert panel._asr_engine_guidance.isVisibleTo(panel)


def test_engine_available_enables_download_and_hides_guidance(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(f"{_PANEL_MOD}._engine.available", lambda: True)
    monkeypatch.setattr(f"{_PANEL_MOD}.model_manager.is_downloaded", lambda name, root: False)
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(AnkiMinerConfig(asr_models_root=tmp_path))

    assert panel.download_model_button.isEnabled()
    assert not panel._asr_engine_guidance.isVisibleTo(panel)
    assert "not downloaded" in panel.model_status_label.text().lower()


def test_download_click_emits_when_engine_available(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(f"{_PANEL_MOD}._engine.available", lambda: True)
    monkeypatch.setattr(f"{_PANEL_MOD}.model_manager.is_downloaded", lambda name, root: False)
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(AnkiMinerConfig(asr_model="small", asr_models_root=tmp_path))

    received: list[str] = []
    panel.asr_download_requested.connect(received.append)
    panel.download_model_button.click()

    assert received == ["small"]
    assert not panel.download_model_button.isEnabled()  # disabled in flight


def test_download_click_noop_when_engine_unavailable(qtbot, tmp_path, monkeypatch):
    """A direct click handler call must not emit when the engine is missing."""
    monkeypatch.setattr(f"{_PANEL_MOD}._engine.available", lambda: False)
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(AnkiMinerConfig(asr_models_root=tmp_path))

    received: list[str] = []
    panel.asr_download_requested.connect(received.append)
    panel._on_download_clicked()

    assert received == []


def test_engine_guidance_command_copies_to_clipboard(qtbot, monkeypatch):
    from PyQt6.QtWidgets import QApplication

    monkeypatch.setattr(f"{_PANEL_MOD}._engine.available", lambda: False)
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)

    panel._copy_to_clipboard('pip install "anki-miner[asr]"')
    clipboard = QApplication.clipboard()
    assert clipboard is not None
    assert clipboard.text() == 'pip install "anki-miner[asr]"'


# ---------------------------------------------------------------------------
# alass in-app download
# ---------------------------------------------------------------------------


def test_alass_download_button_emits_when_supported(qtbot, monkeypatch):
    monkeypatch.setattr(f"{_PANEL_MOD}.alass_installer.alass_install_supported", lambda: True)
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)

    received: list[None] = []
    panel.alass_download_requested.connect(lambda: received.append(None))
    panel.download_alass_button.click()

    assert len(received) == 1
    assert not panel.download_alass_button.isEnabled()  # disabled in flight


def test_alass_status_reflects_installed_state(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(f"{_PANEL_MOD}.alass_installer.alass_install_supported", lambda: True)
    monkeypatch.setattr(f"{_PANEL_MOD}.alass_installer.is_installed", lambda root: True)
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(AnkiMinerConfig(bin_root=tmp_path))

    assert "downloaded" in panel.alass_status_label.text().lower()
    assert panel.download_alass_button.isEnabled()


def test_unsupported_platform_has_no_alass_button(qtbot, monkeypatch):
    """On macOS (unsupported) the panel shows guidance, not a download button."""
    monkeypatch.setattr(f"{_PANEL_MOD}.alass_installer.alass_install_supported", lambda: False)
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)

    assert not hasattr(panel, "download_alass_button")
    # set_alass_status is a safe no-op when unsupported.
    panel.set_alass_status("anything")  # must not raise
