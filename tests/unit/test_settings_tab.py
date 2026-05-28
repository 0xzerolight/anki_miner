"""Tests for the settings tab, focused on the YouTube settings panel wiring."""

from __future__ import annotations

from dataclasses import replace

import pytest
from PyQt6.QtWidgets import QApplication

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.panels.youtube_settings_panel import YouTubeSettingsPanel
from anki_miner.gui.widgets.settings_tab import SettingsTab

# QApplication required for any Qt widget test.
_app = QApplication.instance() or QApplication([])


@pytest.fixture
def tab(test_config: AnkiMinerConfig):
    """Instantiate a SettingsTab against the shared test config."""
    widget = SettingsTab(test_config)
    yield widget
    widget.deleteLater()


class TestYouTubePanelDefaults:
    """Default widget state should match the current config."""

    def test_cookies_combo_defaults_to_none(self, tab):
        panel = tab.youtube_panel
        assert panel.get_cookies_from_browser() is None
        assert panel.cookies_browser_combo.currentText() == "None"

    def test_max_duration_defaults_to_120_minutes(self, tab):
        panel = tab.youtube_panel
        # test_config does not override youtube_max_duration_s, so default 7200s.
        assert panel.max_duration_spinbox.value() == 120
        assert panel.get_max_duration_seconds() == 7200


class TestYouTubePanelValueHelpers:
    """set_* / get_* helpers round-trip config values correctly."""

    @pytest.mark.parametrize(
        "value,expected_label",
        [
            (None, "None"),
            ("firefox", "Firefox"),
            ("chrome", "Chrome"),
            ("chromium", "Chromium"),
            ("edge", "Edge"),
        ],
    )
    def test_set_and_get_cookies_browser(self, value, expected_label):
        panel = YouTubeSettingsPanel()
        try:
            panel.set_cookies_from_browser(value)
            assert panel.cookies_browser_combo.currentText() == expected_label
            assert panel.get_cookies_from_browser() == value
        finally:
            panel.deleteLater()

    def test_unknown_cookie_value_falls_back_to_none(self):
        panel = YouTubeSettingsPanel()
        try:
            panel.set_cookies_from_browser("opera")  # type: ignore[arg-type]
            assert panel.get_cookies_from_browser() is None
        finally:
            panel.deleteLater()

    @pytest.mark.parametrize(
        "seconds,expected_minutes",
        [
            (60, 1),
            (3600, 60),
            (7200, 120),
            (90, 2),  # rounds up
            (0, 1),  # clamped to the spinbox minimum
            (36000, 600),
            (36001, 600),  # clamped to the spinbox maximum
        ],
    )
    def test_set_and_get_max_duration(self, seconds, expected_minutes):
        panel = YouTubeSettingsPanel()
        try:
            panel.set_max_duration_seconds(seconds)
            assert panel.max_duration_spinbox.value() == expected_minutes
            assert panel.get_max_duration_seconds() == expected_minutes * 60
        finally:
            panel.deleteLater()


class TestSettingsTabRoundTrip:
    """Editing widgets and clicking Save should propagate to config_changed."""

    def test_save_emits_updated_youtube_fields(self, tab, monkeypatch):
        # Stub QMessageBox.information so the test doesn't block on a dialog.
        from PyQt6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)

        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)

        tab.youtube_panel.set_cookies_from_browser("firefox")
        tab.youtube_panel.max_duration_spinbox.setValue(60)

        tab._on_save_clicked()

        assert len(received) == 1
        new_config = received[0]
        assert new_config.youtube_cookies_from_browser == "firefox"
        assert new_config.youtube_max_duration_s == 3600

    def test_load_config_reflects_existing_values(self, test_config):
        cfg = replace(
            test_config,
            youtube_cookies_from_browser="chrome",
            youtube_max_duration_s=1800,
        )
        widget = SettingsTab(cfg)
        try:
            assert widget.youtube_panel.get_cookies_from_browser() == "chrome"
            assert widget.youtube_panel.get_max_duration_seconds() == 1800
            assert widget.youtube_panel.max_duration_spinbox.value() == 30
        finally:
            widget.deleteLater()


class TestIPlusOneFilterRoundTrip:
    """Load/save round-trip for the i+1 sentence filter checkbox."""

    def test_loads_use_i_plus_one_filter_from_config(self, test_config: AnkiMinerConfig):
        cfg_on = replace(test_config, use_i_plus_one_filter=True)
        widget = SettingsTab(cfg_on)
        try:
            assert widget.filtering_panel.use_i_plus_one_checkbox.isChecked() is True
        finally:
            widget.deleteLater()

        cfg_off = replace(test_config, use_i_plus_one_filter=False)
        widget = SettingsTab(cfg_off)
        try:
            assert widget.filtering_panel.use_i_plus_one_checkbox.isChecked() is False
        finally:
            widget.deleteLater()

    def test_saves_use_i_plus_one_filter_to_config(self, tab, monkeypatch):
        from PyQt6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)

        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)

        tab.filtering_panel.use_i_plus_one_checkbox.setChecked(True)
        tab._on_save_clicked()

        assert len(received) == 1
        assert received[0].use_i_plus_one_filter is True

        tab.filtering_panel.use_i_plus_one_checkbox.setChecked(False)
        tab._on_save_clicked()

        assert len(received) == 2
        assert received[1].use_i_plus_one_filter is False


class TestAnkiTagsRoundTrip:
    """Load/save round-trip for the anki_tags QLineEdit on the Anki settings panel."""

    def test_loads_anki_tags_from_config(self, test_config: AnkiMinerConfig):
        cfg = replace(test_config, anki_tags="custom tag")
        widget = SettingsTab(cfg)
        try:
            assert widget.anki_panel.anki_tags_input.text() == "custom tag"
        finally:
            widget.deleteLater()

    def test_saves_anki_tags_to_config(self, tab, monkeypatch):
        from PyQt6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)

        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)

        tab.anki_panel.anki_tags_input.setText("new-tag another")
        tab._on_save_clicked()

        assert len(received) == 1
        assert received[0].anki_tags == "new-tag another"


class TestSentenceLengthFilterRoundTrip:
    """Load/save round-trip for the sentence-length filter widgets (Issue #33)."""

    def test_loads_sentence_length_filter_from_config(self, test_config: AnkiMinerConfig):
        cfg = replace(
            test_config,
            use_sentence_length_filter=True,
            max_sentence_duration_seconds=7.5,
            max_sentence_chars=60,
        )
        widget = SettingsTab(cfg)
        try:
            assert widget.filtering_panel.use_sentence_length_checkbox.isChecked() is True
            assert widget.filtering_panel.max_sentence_duration_spinbox.value() == pytest.approx(7.5)
            assert widget.filtering_panel.max_sentence_chars_spinbox.value() == 60
        finally:
            widget.deleteLater()

    def test_saves_sentence_length_filter_to_config(self, tab, monkeypatch):
        from PyQt6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)

        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)

        tab.filtering_panel.use_sentence_length_checkbox.setChecked(True)
        tab.filtering_panel.max_sentence_duration_spinbox.setValue(7.5)
        tab.filtering_panel.max_sentence_chars_spinbox.setValue(60)
        tab._on_save_clicked()

        assert len(received) == 1
        assert received[0].use_sentence_length_filter is True
        assert received[0].max_sentence_duration_seconds == pytest.approx(7.5)
        assert received[0].max_sentence_chars == 60


class TestDictsRootRoundTrip:
    """Load/save round-trip for the Issue #45 dictionary storage folder picker."""

    def test_loads_dicts_root_from_config(self, test_config: AnkiMinerConfig, tmp_path):
        custom = tmp_path / "custom_dicts"
        custom.mkdir()
        cfg = replace(test_config, dicts_root=custom)
        widget = SettingsTab(cfg)
        try:
            assert widget.dictionary_panel.get_dicts_root() == custom
        finally:
            widget.deleteLater()

    def test_save_propagates_new_dicts_root(self, test_config: AnkiMinerConfig, tmp_path, monkeypatch):
        from PyQt6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)

        starting = tmp_path / "starting"
        starting.mkdir()
        cfg = replace(test_config, dicts_root=starting)
        widget = SettingsTab(cfg)
        try:
            received: list[AnkiMinerConfig] = []
            widget.config_changed.connect(received.append)

            new_root = tmp_path / "new_root"
            new_root.mkdir()
            widget.dictionary_panel.dicts_root_selector.set_path(str(new_root))

            widget._on_save_clicked()

            assert len(received) == 1
            assert received[0].dicts_root == new_root
        finally:
            widget.deleteLater()

    def test_save_rejects_nonexistent_dicts_root(self, test_config: AnkiMinerConfig, tmp_path, monkeypatch):
        """Picking a path that vanished between selection and save must surface a
        warning and abort — never write Path('') to the config."""
        from PyQt6.QtWidgets import QMessageBox

        starting = tmp_path / "starting"
        starting.mkdir()
        cfg = replace(test_config, dicts_root=starting)
        widget = SettingsTab(cfg)
        try:
            warnings: list[tuple] = []
            monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: warnings.append(args) or 0)
            monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)

            received: list[AnkiMinerConfig] = []
            widget.config_changed.connect(received.append)

            widget.dictionary_panel.dicts_root_selector.set_path(str(tmp_path / "does_not_exist"))
            widget._on_save_clicked()

            assert received == [], "save must abort when dicts_root is invalid"
            assert warnings, "user must see a warning explaining the rejection"
        finally:
            widget.deleteLater()

    def test_save_rejects_unwritable_dicts_root(self, test_config: AnkiMinerConfig, tmp_path, monkeypatch):
        """A read-only directory must be rejected at Save so the user is not
        silently committed to a path the importers can't write to."""
        from PyQt6.QtWidgets import QMessageBox

        starting = tmp_path / "starting"
        starting.mkdir()
        readonly = tmp_path / "readonly"
        readonly.mkdir()
        cfg = replace(test_config, dicts_root=starting)
        widget = SettingsTab(cfg)
        try:
            warnings: list[tuple] = []
            monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: warnings.append(args) or 0)
            monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)
            # Force os.access to claim the path is not writable so the test is
            # portable across CI runners that may ignore chmod (e.g. root in
            # Docker, Windows ACLs). The validation logic only consults
            # os.access — patching it covers the production code path.
            import anki_miner.gui.widgets.settings_tab as st_mod

            def _no_write(path, mode):
                return str(path) != str(readonly)

            monkeypatch.setattr(st_mod.os, "access", _no_write)

            received: list[AnkiMinerConfig] = []
            widget.config_changed.connect(received.append)

            widget.dictionary_panel.dicts_root_selector.set_path(str(readonly))
            widget._on_save_clicked()

            assert received == [], "save must abort when dicts_root is not writable"
            assert warnings, "user must see a warning explaining the rejection"
        finally:
            widget.deleteLater()
