"""Tests for MediaSettingsPanel: clip length, animated size and reading TTS."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import create_default_config
from anki_miner.gui.widgets.panels import media_settings_panel
from anki_miner.gui.widgets.panels.media_settings_panel import MediaSettingsPanel
from anki_miner.languages.profile import AudioDefaults
from anki_miner.languages.switching import switch_language


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


def _tts_config(code: str, enabled: bool, google: bool, papago: bool):
    return replace(
        switch_language(create_default_config(), code),
        reading_tts_enabled=enabled,
        reading_tts_google_enabled=google,
        reading_tts_papago_enabled=papago,
    )


def _tts_items(panel: MediaSettingsPanel) -> tuple[list[str], list[str]]:
    combo = panel.reading_tts_combo
    return (
        [combo.itemText(i) for i in range(combo.count())],
        [combo.itemData(i) for i in range(combo.count())],
    )


def _tts_out(panel: MediaSettingsPanel) -> tuple[bool, bool, bool]:
    out = panel.contribute(create_default_config())
    return (out.reading_tts_enabled, out.reading_tts_google_enabled, out.reading_tts_papago_enabled)


def _pick(panel: MediaSettingsPanel, key: str) -> None:
    idx = panel.reading_tts_combo.findData(key)
    panel.reading_tts_combo.setCurrentIndex(idx)
    panel.reading_tts_combo.activated.emit(idx)


class TestReadingTtsComboFollowsTheLanguage:
    """The combo offers only the voices the mining language has.

    ``reading_tts_google_enabled`` is the web-voice leg (Google, or Edge for a
    language Google cannot speak), so a language without Papago shows Off plus
    one item on the "google" key, named for the voice the chain will use.
    """

    def test_a_language_with_google_only_offers_off_and_google(self, qtbot):
        panel = MediaSettingsPanel()
        qtbot.addWidget(panel)
        panel.load_from_config(_tts_config("es", True, True, True))

        assert _tts_items(panel) == (["Off", "Google"], ["off", "google"])

    def test_a_language_google_cannot_speak_names_its_edge_voice(self, qtbot):
        panel = MediaSettingsPanel()
        qtbot.addWidget(panel)
        panel.load_from_config(_tts_config("fa", True, True, False))

        assert _tts_items(panel) == (["Off", "Microsoft Edge"], ["off", "google"])
        assert panel.reading_tts_combo.currentData() == "google"

    def test_papago_alone_shows_off_and_round_trips_untouched(self, qtbot):
        panel = MediaSettingsPanel()
        qtbot.addWidget(panel)
        panel.load_from_config(_tts_config("es", True, False, True))

        assert panel.reading_tts_combo.currentData() == "off"
        assert _tts_out(panel) == (True, False, True)

    @pytest.mark.parametrize(
        ("loaded", "expected"),
        [
            ((False, True, False), (True, True, False)),
            ((True, False, True), (True, True, True)),
        ],
    )
    def test_picking_the_web_voice_keeps_the_loaded_papago_flag(self, qtbot, loaded, expected):
        panel = MediaSettingsPanel()
        qtbot.addWidget(panel)
        panel.load_from_config(_tts_config("es", *loaded))

        _pick(panel, "google")

        assert _tts_out(panel) == expected

    def test_a_language_switch_rebuilds_the_items(self, qtbot):
        panel = MediaSettingsPanel()
        qtbot.addWidget(panel)

        panel.load_from_config(_tts_config("ja", True, True, True))
        assert panel.reading_tts_combo.count() == 4
        assert panel.reading_tts_combo.currentData() == "both"

        panel.load_from_config(_tts_config("es", True, True, False))
        assert panel.reading_tts_combo.count() == 2
        assert panel.reading_tts_combo.currentData() == "google"

        panel.load_from_config(_tts_config("ja", True, False, True))
        assert _tts_items(panel) == (
            ["Off", "Google, then Papago", "Google only", "Papago only"],
            ["off", "both", "google", "papago"],
        )
        assert panel.reading_tts_combo.currentData() == "papago"

    def test_a_language_with_no_web_voice_offers_only_off(self, qtbot, monkeypatch):
        """No real profile reaches this (test_edge_voice_contract), but the
        combo must still show a real item, never an empty selection."""
        stub = SimpleNamespace(
            audio=AudioDefaults(
                gtts_lang="",
                cache_stem_prefix="g",
                sentence_cache_stem_prefix="s",
                custom_fetcher_language="xx",
            )
        )
        monkeypatch.setattr(media_settings_panel, "get_profile", lambda _code: stub)
        panel = MediaSettingsPanel()
        qtbot.addWidget(panel)
        panel.load_from_config(_tts_config("es", True, True, False))

        assert _tts_items(panel) == (["Off"], ["off"])
        assert panel.reading_tts_combo.currentData() == "off"
        assert _tts_out(panel) == (True, True, False)


def test_sentence_tts_row_is_labelled_text_to_speech(qtbot):
    """C02 + D3: a short label named for what the row does; the manga/books scope lives in its helper."""
    from PyQt6.QtWidgets import QLabel

    panel = MediaSettingsPanel()
    qtbot.addWidget(panel)
    texts = [label.text() for label in panel.findChildren(QLabel)]

    assert "Text-to-speech:" in texts
    assert not any("Spoken sentences for manga" in text for text in texts)
