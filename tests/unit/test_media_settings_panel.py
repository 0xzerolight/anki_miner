"""Tests for MediaSettingsPanel: clip length, animated size and reading TTS."""

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


def test_clip_length_lowest_value_means_same_as_sentence_audio(qtbot):
    """C14: "Match audio duration" folded into the clip length spinbox."""
    panel = MediaSettingsPanel()
    qtbot.addWidget(panel)
    spin = panel.animated_duration_spinbox
    assert not hasattr(panel, "animated_match_audio_checkbox")
    assert spin.minimum() == 0.0
    assert spin.specialValueText() == "Same as sentence audio"


def test_choosing_same_as_audio_keeps_the_stored_length(qtbot):
    panel = MediaSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(
        replace(create_default_config(), screenshot_animated=True, screenshot_animated_clip_duration=3.5)
    )

    panel.animated_duration_spinbox.setValue(0.0)
    out = panel.contribute(create_default_config())

    assert out.screenshot_animated_match_audio is True
    assert out.screenshot_animated_clip_duration == 3.5


def test_stepping_up_from_same_as_audio_resumes_the_users_length(qtbot):
    panel = MediaSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(
        replace(
            create_default_config(),
            screenshot_animated=True,
            screenshot_animated_clip_duration=3.5,
            screenshot_animated_match_audio=True,
        )
    )
    spin = panel.animated_duration_spinbox
    assert spin.value() == 0.0

    spin.stepBy(1)

    assert spin.value() == 3.5
    out = panel.contribute(create_default_config())
    assert out.screenshot_animated_match_audio is False
    assert out.screenshot_animated_clip_duration == 3.5


def _loaded(qtbot, length: float) -> MediaSettingsPanel:
    panel = MediaSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(
        replace(create_default_config(), screenshot_animated=True, screenshot_animated_clip_duration=length)
    )
    return panel


def _step_to_same_as_audio(spin) -> None:
    while spin.value() > spin.minimum():
        spin.stepBy(-1)


def test_stepping_down_to_same_as_audio_keeps_the_stored_length(qtbot):
    """Edge1: the way down passes through 0.5, and none of those steps is the user's length."""
    panel = _loaded(qtbot, 3.0)
    spin = panel.animated_duration_spinbox

    _step_to_same_as_audio(spin)

    out = panel.contribute(create_default_config())
    assert out.screenshot_animated_match_audio is True
    assert out.screenshot_animated_clip_duration == 3.0
    spin.stepBy(1)
    assert spin.value() == 3.0


def test_a_stepped_length_the_user_settles_on_is_kept(qtbot):
    panel = _loaded(qtbot, 3.0)
    spin = panel.animated_duration_spinbox
    spin.stepBy(-2)
    spin.editingFinished.emit()  # focus left the field on 2.0

    _step_to_same_as_audio(spin)

    assert panel.contribute(create_default_config()).screenshot_animated_clip_duration == 2.0


def test_a_typed_length_is_kept(qtbot):
    panel = _loaded(qtbot, 3.0)
    spin = panel.animated_duration_spinbox
    spin.setValue(4.5)

    spin.setValue(spin.minimum())

    assert panel.contribute(create_default_config()).screenshot_animated_clip_duration == 4.5


def test_clip_length_follows_the_animated_switch(qtbot):
    panel = MediaSettingsPanel()
    qtbot.addWidget(panel)
    panel.animated_checkbox.setChecked(False)
    assert panel.animated_duration_spinbox.isEnabled() is False
    panel.animated_checkbox.setChecked(True)
    assert panel.animated_duration_spinbox.isEnabled() is True


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


def test_custom_then_preset_then_custom_reload_shows_exactly_one_custom_item(qtbot):
    """Custom -> a real preset -> a different custom reload never leaves two
    stale "Custom (...)" entries, or the wrong one, behind."""
    panel = MediaSettingsPanel()
    qtbot.addWidget(panel)

    # 1. Load custom triple A -- the Custom item appears.
    triple_a = (18, 540, 35)
    cfg_a = replace(
        create_default_config(),
        screenshot_animated_fps=triple_a[0],
        screenshot_animated_height=triple_a[1],
        screenshot_animated_quality=triple_a[2],
    )
    panel.load_from_config(cfg_a)
    assert panel.animated_size_combo.currentData() == "custom"
    assert panel.animated_size_combo.findData("custom") != -1

    # 2. Pick High -- the Custom item is dropped.
    idx = panel.animated_size_combo.findData("high")
    panel.animated_size_combo.setCurrentIndex(idx)
    panel.animated_size_combo.activated.emit(idx)
    assert panel.animated_size_combo.currentData() == "high"
    assert panel.animated_size_combo.findData("custom") == -1

    # 3. Reload a config with a different custom triple B -- exactly one
    # Custom item, showing B's values.
    triple_b = (22, 900, 65)
    cfg_b = replace(
        create_default_config(),
        screenshot_animated_fps=triple_b[0],
        screenshot_animated_height=triple_b[1],
        screenshot_animated_quality=triple_b[2],
    )
    panel.load_from_config(cfg_b)

    custom_items = [
        i for i in range(panel.animated_size_combo.count()) if panel.animated_size_combo.itemData(i) == "custom"
    ]
    assert len(custom_items) == 1
    assert panel.animated_size_combo.currentData() == "custom"
    label = panel.animated_size_combo.currentText()
    assert "22" in label and "900" in label and "65" in label

    # 4. contribute() returns B.
    out = panel.contribute(cfg_b)
    assert (
        out.screenshot_animated_fps,
        out.screenshot_animated_height,
        out.screenshot_animated_quality,
    ) == triple_b


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


class TestReadingTtsCombo:
    """The sentence-TTS combo, folded from the Audio page's 3-control block (T11)."""

    @pytest.mark.parametrize(
        ("enabled", "google", "papago", "expected_data"),
        [
            (False, True, True, "off"),
            (False, False, False, "off"),
            (True, True, True, "both"),
            (True, True, False, "google"),
            (True, False, True, "papago"),
            (True, False, False, "off"),
        ],
    )
    def test_load_mapping(self, qtbot, enabled, google, papago, expected_data):
        panel = MediaSettingsPanel()
        qtbot.addWidget(panel)

        panel.load_from_config(
            replace(
                create_default_config(),
                reading_tts_enabled=enabled,
                reading_tts_google_enabled=google,
                reading_tts_papago_enabled=papago,
            )
        )

        assert panel.reading_tts_combo.currentData() == expected_data

    def test_untouched_combo_contributes_the_loaded_triple_unchanged(self, qtbot):
        """Even a (True, False, False) triple round-trips until the combo is touched."""
        panel = MediaSettingsPanel()
        qtbot.addWidget(panel)
        cfg = replace(
            create_default_config(),
            reading_tts_enabled=True,
            reading_tts_google_enabled=False,
            reading_tts_papago_enabled=False,
        )
        panel.load_from_config(cfg)

        # An unrelated edit on the same panel must not touch the combo's state.
        panel.audio_padding_spinbox.setValue(0.5)

        out = panel.contribute(create_default_config())
        assert (
            out.reading_tts_enabled,
            out.reading_tts_google_enabled,
            out.reading_tts_papago_enabled,
        ) == (True, False, False)

    def test_picking_google_only_writes_its_triple(self, qtbot):
        panel = MediaSettingsPanel()
        qtbot.addWidget(panel)
        panel.load_from_config(create_default_config())

        idx = panel.reading_tts_combo.findData("google")
        panel.reading_tts_combo.setCurrentIndex(idx)
        panel.reading_tts_combo.activated.emit(idx)

        out = panel.contribute(create_default_config())
        assert (
            out.reading_tts_enabled,
            out.reading_tts_google_enabled,
            out.reading_tts_papago_enabled,
        ) == (True, True, False)

    def test_picking_off_keeps_the_loaded_provider_pair(self, qtbot):
        """Off only flips ``enabled``; the provider pair is the one a re-enable resumes with."""
        panel = MediaSettingsPanel()
        qtbot.addWidget(panel)
        cfg = replace(
            create_default_config(),
            reading_tts_enabled=True,
            reading_tts_google_enabled=False,
            reading_tts_papago_enabled=True,
        )
        panel.load_from_config(cfg)

        idx = panel.reading_tts_combo.findData("off")
        panel.reading_tts_combo.setCurrentIndex(idx)
        panel.reading_tts_combo.activated.emit(idx)

        out = panel.contribute(create_default_config())
        assert (
            out.reading_tts_enabled,
            out.reading_tts_google_enabled,
            out.reading_tts_papago_enabled,
        ) == (False, False, True)

    def test_a_reload_resets_the_touched_flag(self, qtbot):
        """A later load_from_config re-establishes a fresh untouched baseline,
        even after the user already touched the combo once this session."""
        panel = MediaSettingsPanel()
        qtbot.addWidget(panel)
        panel.load_from_config(create_default_config())

        # Touch the combo (user picks Off).
        idx = panel.reading_tts_combo.findData("off")
        panel.reading_tts_combo.setCurrentIndex(idx)
        panel.reading_tts_combo.activated.emit(idx)
        assert panel._tts_touched is True

        # A reload (e.g. a profile switch) must reset the touched flag, so the
        # freshly loaded triple round-trips unchanged until touched again.
        panel.load_from_config(
            replace(
                create_default_config(),
                reading_tts_enabled=True,
                reading_tts_google_enabled=False,
                reading_tts_papago_enabled=False,
            )
        )
        assert panel._tts_touched is False

        out = panel.contribute(create_default_config())
        assert (
            out.reading_tts_enabled,
            out.reading_tts_google_enabled,
            out.reading_tts_papago_enabled,
        ) == (True, False, False)

    def test_programmatic_load_never_marks_the_combo_touched(self, qtbot):
        """``activated`` is user-only; ``setCurrentIndex`` from a load must not arm it."""
        panel = MediaSettingsPanel()
        qtbot.addWidget(panel)
        panel.load_from_config(
            replace(create_default_config(), reading_tts_enabled=True, reading_tts_google_enabled=True)
        )
        assert panel._tts_touched is False


def test_sentence_tts_row_is_labelled_text_to_speech(qtbot):
    """C02 + D3: a short label named for what the row does; the manga/books scope lives in its helper."""
    from PyQt6.QtWidgets import QLabel

    panel = MediaSettingsPanel()
    qtbot.addWidget(panel)
    texts = [label.text() for label in panel.findChildren(QLabel)]

    assert "Text-to-speech:" in texts
    assert not any("Spoken sentences for manga" in text for text in texts)
