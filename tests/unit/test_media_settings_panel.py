"""Tests for MediaSettingsPanel — Match Audio Duration gating."""

from __future__ import annotations

from dataclasses import replace

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import create_default_config
from anki_miner.gui.widgets.panels.media_settings_panel import MediaSettingsPanel


def test_audio_bitrate_tooltip_merges_helper_and_old_tooltip(qtbot):
    # The redundant explicit setToolTip was folded into the add_field helper
    # during the helper->tooltip migration; the merged text is the one tooltip.
    panel = MediaSettingsPanel()
    qtbot.addWidget(panel)
    assert panel.audio_bitrate_spinbox.toolTip() == (
        "Higher = better quality, larger files. " "64-96 kbps Opus or 128-192 kbps MP3 are good defaults."
    )


def test_match_audio_toggle_disables_duration_spinbox(qtbot):
    """Ticking match-audio while the feature is on disables the duration spinbox."""
    panel = MediaSettingsPanel()
    qtbot.addWidget(panel)

    panel.animated_checkbox.setChecked(True)
    assert panel.animated_duration_spinbox.isEnabled() is True

    panel.animated_match_audio_checkbox.setChecked(True)
    assert panel.animated_duration_spinbox.isEnabled() is False

    panel.animated_match_audio_checkbox.setChecked(False)
    assert panel.animated_duration_spinbox.isEnabled() is True


def test_duration_spinbox_stays_disabled_when_feature_off(qtbot):
    """If the parent animated feature is off, match-audio cannot enable the spinbox."""
    panel = MediaSettingsPanel()
    qtbot.addWidget(panel)

    panel.animated_checkbox.setChecked(False)
    assert panel.animated_duration_spinbox.isEnabled() is False

    panel.animated_match_audio_checkbox.setChecked(True)
    assert panel.animated_duration_spinbox.isEnabled() is False

    panel.animated_match_audio_checkbox.setChecked(False)
    assert panel.animated_duration_spinbox.isEnabled() is False


def test_match_audio_disabled_when_feature_off(qtbot):
    """The match-audio checkbox itself is gated by the parent animated feature."""
    panel = MediaSettingsPanel()
    qtbot.addWidget(panel)

    panel.animated_checkbox.setChecked(False)
    assert panel.animated_match_audio_checkbox.isEnabled() is False

    panel.animated_checkbox.setChecked(True)
    assert panel.animated_match_audio_checkbox.isEnabled() is True


def test_default_triple_is_balanced(qtbot):
    """The pre-preset defaults (20 fps / 720 px / quality 30) load as Balanced."""
    panel = MediaSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(create_default_config())
    assert panel.animated_size_combo.currentData() == "balanced"


def test_custom_triple_survives_unrelated_edit(qtbot):
    """A triple matching no preset shows as Custom and isn't rewritten by another edit."""
    panel = MediaSettingsPanel()
    qtbot.addWidget(panel)
    cfg = replace(
        create_default_config(),
        screenshot_animated_fps=25,
        screenshot_animated_height=600,
        screenshot_animated_quality=45,
    )
    panel.load_from_config(cfg)
    assert panel.animated_size_combo.currentData() == "custom"
    assert "25" in panel.animated_size_combo.currentText()

    panel.audio_padding_spinbox.setValue(0.5)

    out = panel.contribute(cfg)
    assert (
        out.screenshot_animated_fps,
        out.screenshot_animated_height,
        out.screenshot_animated_quality,
    ) == (25, 600, 45)


def test_picking_high_writes_its_triple(qtbot):
    """Selecting the High preset writes its (fps, height, quality) triple."""
    panel = MediaSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(create_default_config())
    panel.animated_size_combo.setCurrentIndex(panel.animated_size_combo.findData("high"))

    out = panel.contribute(create_default_config())
    assert (
        out.screenshot_animated_fps,
        out.screenshot_animated_height,
        out.screenshot_animated_quality,
    ) == (24, 1080, 50)
