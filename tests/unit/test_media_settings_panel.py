"""Tests for MediaSettingsPanel — Match Audio Duration gating."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication

from anki_miner.gui.widgets.panels.media_settings_panel import MediaSettingsPanel

# QApplication must exist before any widget is instantiated.
_app = QApplication.instance() or QApplication([])


def test_audio_bitrate_tooltip_merges_helper_and_old_tooltip():
    # The redundant explicit setToolTip was folded into the add_field helper
    # during the helper->tooltip migration; the merged text is the one tooltip.
    panel = MediaSettingsPanel()
    assert panel.audio_bitrate_spinbox.toolTip() == (
        "Higher = better quality, larger files. " "64-96 kbps Opus or 128-192 kbps MP3 are good defaults."
    )


def test_match_audio_toggle_disables_duration_spinbox():
    """Ticking match-audio while the feature is on disables the duration spinbox."""
    panel = MediaSettingsPanel()

    panel.animated_checkbox.setChecked(True)
    assert panel.animated_duration_spinbox.isEnabled() is True

    panel.animated_match_audio_checkbox.setChecked(True)
    assert panel.animated_duration_spinbox.isEnabled() is False

    panel.animated_match_audio_checkbox.setChecked(False)
    assert panel.animated_duration_spinbox.isEnabled() is True


def test_duration_spinbox_stays_disabled_when_feature_off():
    """If the parent animated feature is off, match-audio cannot enable the spinbox."""
    panel = MediaSettingsPanel()

    panel.animated_checkbox.setChecked(False)
    assert panel.animated_duration_spinbox.isEnabled() is False

    panel.animated_match_audio_checkbox.setChecked(True)
    assert panel.animated_duration_spinbox.isEnabled() is False

    panel.animated_match_audio_checkbox.setChecked(False)
    assert panel.animated_duration_spinbox.isEnabled() is False


def test_match_audio_disabled_when_feature_off():
    """The match-audio checkbox itself is gated by the parent animated feature."""
    panel = MediaSettingsPanel()

    panel.animated_checkbox.setChecked(False)
    assert panel.animated_match_audio_checkbox.isEnabled() is False

    panel.animated_checkbox.setChecked(True)
    assert panel.animated_match_audio_checkbox.isEnabled() is True
