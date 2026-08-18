"""Tests for the settings tab, focused on the YouTube settings panel wiring."""

from __future__ import annotations

import contextlib
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig, ChainEntry
from anki_miner.gui.widgets.panels.youtube_settings_panel import YouTubeSettingsPanel
from anki_miner.gui.widgets.settings_tab import SettingsTab


@pytest.fixture
def tab(test_config: AnkiMinerConfig, qtbot):
    """Instantiate a SettingsTab against the shared test config."""
    widget = SettingsTab(test_config)
    qtbot.addWidget(widget)
    yield widget
    # _on_save_clicked reconciles styling, which spawns a short-lived AnkiConnect
    # worker; join it (and any other probe workers) and flush queued signals so a
    # late status update can't fire into a torn-down QLabel. Mirrors closeEvent.
    widget.shutdown()
    for w in widget.iter_close_workers():
        if w is not None:
            w.wait(3000)
    qtbot.wait(10)
    # The widget may already be reaped by pytest-qt's cleanup net during the
    # flush above; deleteLater on a dead C++ object then raises.
    with contextlib.suppress(RuntimeError):
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

    def test_playlist_max_defaults_to_100(self, tab):
        panel = tab.youtube_panel
        # test_config does not override youtube_playlist_max, so default is 100.
        assert panel.playlist_max_spinbox.value() == 100
        assert panel.get_playlist_max() == 100


def test_config_memory_not_published_when_persistence_listener_raises(tab, monkeypatch):
    original = tab.config
    errors: list[BaseException] = []

    def fail_save(config):
        raise OSError("disk full")

    tab.config_changed.connect(fail_save)
    monkeypatch.setattr(sys, "excepthook", lambda exc_type, exc, traceback: errors.append(exc))

    tab._on_theme_state_changed("dark", ())

    assert len(errors) == 1
    assert str(errors[0]) == "disk full"
    assert tab.config is original


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
            ("brave", "Brave"),
            ("opera", "Opera"),
            ("vivaldi", "Vivaldi"),
            ("safari", "Safari"),
        ],
    )
    def test_set_and_get_cookies_browser(self, value, expected_label, qtbot):
        panel = YouTubeSettingsPanel()
        qtbot.addWidget(panel)
        try:
            panel.set_cookies_from_browser(value)
            assert panel.cookies_browser_combo.currentText() == expected_label
            assert panel.get_cookies_from_browser() == value
        finally:
            panel.deleteLater()

    def test_unknown_cookie_value_falls_back_to_none(self, qtbot):
        panel = YouTubeSettingsPanel()
        qtbot.addWidget(panel)
        try:
            panel.set_cookies_from_browser("netscape")  # type: ignore[arg-type]
            assert panel.get_cookies_from_browser() is None
        finally:
            panel.deleteLater()

    def test_set_and_get_cookies_file_round_trip(self, tmp_path, qtbot):
        panel = YouTubeSettingsPanel()
        qtbot.addWidget(panel)
        try:
            cookies = tmp_path / "cookies.txt"
            panel.set_cookies_file(cookies)
            assert panel.get_cookies_file() == str(cookies)
        finally:
            panel.deleteLater()

    def test_cookies_file_defaults_to_empty(self, qtbot):
        panel = YouTubeSettingsPanel()
        qtbot.addWidget(panel)
        try:
            assert panel.get_cookies_file() == ""
        finally:
            panel.deleteLater()

    def test_set_cookies_file_none_clears_field(self, tmp_path, qtbot):
        panel = YouTubeSettingsPanel()
        qtbot.addWidget(panel)
        try:
            panel.set_cookies_file(tmp_path / "cookies.txt")
            panel.set_cookies_file(None)
            assert panel.get_cookies_file() == ""
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
    def test_set_and_get_max_duration(self, seconds, expected_minutes, qtbot):
        panel = YouTubeSettingsPanel()
        qtbot.addWidget(panel)
        try:
            panel.set_max_duration_seconds(seconds)
            assert panel.max_duration_spinbox.value() == expected_minutes
            assert panel.get_max_duration_seconds() == expected_minutes * 60
        finally:
            panel.deleteLater()

    @pytest.mark.parametrize(
        "value,expected",
        [
            (1, 1),
            (100, 100),
            (1000, 1000),
            (0, 1),  # clamped to spinbox minimum
            (1001, 1000),  # clamped to spinbox maximum
            (500, 500),
        ],
    )
    def test_set_and_get_playlist_max(self, value, expected, qtbot):
        panel = YouTubeSettingsPanel()
        qtbot.addWidget(panel)
        try:
            panel.set_playlist_max(value)
            assert panel.playlist_max_spinbox.value() == expected
            assert panel.get_playlist_max() == expected
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

        tab.commit_settings()

        assert len(received) == 1
        new_config = received[0]
        assert new_config.youtube_cookies_from_browser == "firefox"
        assert new_config.youtube_max_duration_s == 3600

    def test_save_emits_playlist_max(self, tab, monkeypatch):
        from PyQt6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)

        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)

        tab.youtube_panel.set_playlist_max(250)
        tab.commit_settings()

        assert len(received) == 1
        assert received[0].youtube_playlist_max == 250

    def test_save_emits_auto_update_ytdlp(self, tab, monkeypatch):
        """The checkbox had no UI at all; gui_config.json was the only way in."""
        from PyQt6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)

        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)

        tab.youtube_panel.set_auto_update_ytdlp(False)
        tab.commit_settings()

        assert len(received) == 1
        assert received[0].auto_update_ytdlp is False

    def test_save_emits_ytdlp_prerelease(self, tab, monkeypatch):
        from PyQt6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)

        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)

        tab.youtube_panel.set_ytdlp_prerelease(True)
        tab.commit_settings()

        assert len(received) == 1
        assert received[0].ytdlp_prerelease is True

    def test_save_emits_ytdlp_location(self, tab, monkeypatch, tmp_path):
        from PyQt6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)

        binary = tmp_path / "my yt-dlp dir " / "yt-dlp"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\n")

        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)

        tab.youtube_panel.set_ytdlp_location(binary)
        tab.commit_settings()

        assert len(received) == 1
        # A trailing space in a directory name must survive: the getter uses
        # path_or_none(), never strip().
        assert received[0].ytdlp_location == binary

    def test_ytdlp_fields_round_trip_through_load_from_config(self, tab, tmp_path):
        binary = tmp_path / "yt-dlp"
        binary.write_text("#!/bin/sh\n")
        config = replace(tab.config, auto_update_ytdlp=False, ytdlp_location=binary)

        tab.youtube_panel.load_from_config(config)

        assert tab.youtube_panel.get_auto_update_ytdlp() is False
        assert tab.youtube_panel.get_ytdlp_location() == str(binary)

    def test_empty_ytdlp_location_clears_the_override(self, tab, monkeypatch):
        from PyQt6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)

        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)

        tab.youtube_panel.set_ytdlp_location("")
        tab.commit_settings()

        assert received[0].ytdlp_location is None

    def test_save_flashes_inline_status_not_popup(self, tab, monkeypatch):
        """A successful save shows the inline label, not a modal popup."""
        from PyQt6.QtWidgets import QMessageBox

        def _fail(*_a, **_k):
            raise AssertionError("save must not show a modal popup")

        monkeypatch.setattr(QMessageBox, "information", _fail)

        assert tab.save_status_label.text() == ""
        tab.commit_settings()

        assert "Saved" in tab.save_status_label.text()
        assert tab._save_status_timer.isActive()

    def test_load_config_reflects_playlist_max(self, test_config, qtbot):
        cfg = replace(test_config, youtube_playlist_max=42)
        widget = SettingsTab(cfg)
        qtbot.addWidget(widget)
        try:
            assert widget.youtube_panel.get_playlist_max() == 42
            assert widget.youtube_panel.playlist_max_spinbox.value() == 42
        finally:
            widget.deleteLater()

    def test_load_config_reflects_existing_values(self, test_config, qtbot):
        cfg = replace(
            test_config,
            youtube_cookies_from_browser="chrome",
            youtube_max_duration_s=1800,
        )
        widget = SettingsTab(cfg)
        qtbot.addWidget(widget)
        try:
            assert widget.youtube_panel.get_cookies_from_browser() == "chrome"
            assert widget.youtube_panel.get_max_duration_seconds() == 1800
            assert widget.youtube_panel.max_duration_spinbox.value() == 30
        finally:
            widget.deleteLater()

    def test_load_config_reflects_cookies_file(self, test_config, tmp_path, qtbot):
        cookies = tmp_path / "cookies.txt"
        cfg = replace(test_config, youtube_cookies_file=cookies)
        widget = SettingsTab(cfg)
        qtbot.addWidget(widget)
        try:
            assert widget.youtube_panel.get_cookies_file() == str(cookies)
        finally:
            widget.deleteLater()

    def test_save_emits_cookies_file_as_path(self, tab, monkeypatch, tmp_path):
        from PyQt6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
        cookies = tmp_path / "cookies.txt"
        cookies.write_text("# Netscape HTTP Cookie File\n")

        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)
        tab.youtube_panel.set_cookies_file(cookies)

        tab.commit_settings()

        assert len(received) == 1
        assert received[0].youtube_cookies_file == cookies

    def test_save_empty_cookies_file_is_none(self, tab, monkeypatch):
        from PyQt6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)
        tab.youtube_panel.set_cookies_file("")

        tab.commit_settings()

        assert len(received) == 1
        assert received[0].youtube_cookies_file is None

    def test_missing_cookies_file_kept_back_without_modal(self, tab, test_config, monkeypatch, tmp_path):
        """Per-field auto-save validation: a missing cookies file keeps its
        last-good value and shows the sticky inline warning — no modal, and the
        rest of the commit still goes through."""
        from PyQt6.QtWidgets import QMessageBox

        def _no_modal(*a, **k):  # pragma: no cover - failure path
            raise AssertionError("no modal may fire during an auto-save commit")

        monkeypatch.setattr(QMessageBox, "warning", _no_modal)
        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)
        tab.youtube_panel.set_cookies_file(tmp_path / "does-not-exist.txt")

        tab.commit_settings()

        assert len(received) == 1, "the commit must still go through"
        assert received[0].youtube_cookies_file == test_config.youtube_cookies_file
        assert "⚠" in tab.save_status_label.text()


class TestSubtitleRegexValidationRevert:
    """An invalid subtitle regex keeps the pattern, toggle AND replacement at
    their last-good values (all three revert together — never a last-good
    pattern paired with a new, never-previewed replacement)."""

    def test_invalid_regex_reverts_pattern_toggle_and_replacement(self, test_config, monkeypatch, qtbot):
        from PyQt6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

        cfg = replace(
            test_config,
            subtitle_regex_filter=r"\(keep\)",
            subtitle_regex_replacement="KEEP",
            use_subtitle_regex_filter=True,
        )
        widget = SettingsTab(cfg)
        qtbot.addWidget(widget)
        try:
            # User edits: an invalid pattern paired with a brand-new replacement.
            widget.filtering_panel.set_subtitle_regex_filter("(")  # unbalanced → re.error
            widget.filtering_panel.set_subtitle_regex_replacement("NEW")
            widget.filtering_panel.set_use_subtitle_regex_filter(True)

            received: list[AnkiMinerConfig] = []
            widget.config_changed.connect(received.append)
            widget.commit_settings()

            assert len(received) == 1
            assert received[0].subtitle_regex_filter == r"\(keep\)"
            assert received[0].use_subtitle_regex_filter is True
            # The replacement must revert too — not the never-previewed "NEW".
            assert received[0].subtitle_regex_replacement == "KEEP"
        finally:
            widget.shutdown()
            for w in widget.iter_close_workers():
                if w is not None:
                    w.wait(3000)
            qtbot.wait(10)
            with contextlib.suppress(RuntimeError):
                widget.deleteLater()


class TestImportInvalidSubtitleRegex:
    """Importing an invalid regex must warn and keep the previous filter."""

    @pytest.mark.parametrize("enabled", [True, False], ids=("enabled", "disabled"))
    def test_import_invalid_regex_warns_and_keeps_previous_filter(
        self, enabled, test_config, monkeypatch, qtbot, tmp_path
    ):
        import json

        from PyQt6.QtWidgets import QMessageBox

        # A config file with an unbalanced group (re.error).
        source = tmp_path / "settings.json"
        source.write_text(
            json.dumps(
                {
                    "subtitle_regex_filter": "(",
                    "subtitle_regex_replacement": "NEW",
                    "use_subtitle_regex_filter": enabled,
                }
            ),
            encoding="utf-8",
        )

        widget = SettingsTab(test_config)
        qtbot.addWidget(widget)
        try:
            monkeypatch.setattr(
                "anki_miner.gui.widgets.settings_tab.file_dialogs.pick_open_file",
                lambda *a, on_done, **k: on_done(str(source)),
            )
            monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
            received: list[AnkiMinerConfig] = []
            widget.config_changed.connect(received.append)

            widget._on_import_settings()

            # The invalid pattern must surface, not be silently applied — as a
            # screen issue on Settings now, not a modal (D24).
            issue = widget.issue_banner().current_issue()
            assert issue is not None
            assert "rejected" in issue.summary
            assert len(received) == 1
            # The prior disabled filter stays intact; invalid imported text is not stored.
            assert received[0].use_subtitle_regex_filter is False
            assert received[0].subtitle_regex_filter == test_config.subtitle_regex_filter
            assert received[0].subtitle_regex_replacement == test_config.subtitle_regex_replacement
        finally:
            widget.shutdown()
            for w in widget.iter_close_workers():
                if w is not None:
                    w.wait(3000)
            qtbot.wait(10)
            with contextlib.suppress(RuntimeError):
                widget.deleteLater()


class TestImportResultFeedback:
    def test_invalid_fields_and_notices_show_information_summary(self, tab, monkeypatch, tmp_path):
        import json

        from PyQt6.QtWidgets import QMessageBox

        source = tmp_path / "old-settings.json"
        source.write_text(
            json.dumps(
                {
                    "anki_miner_settings": 1,
                    "config_schema_version": 1,
                    "settings": {
                        "anki_deck_name": "Imported Deck",
                        "check_for_updates": {"invalid": "bool"},
                        "auto_update_ytdlp": True,
                    },
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "anki_miner.gui.widgets.settings_tab.file_dialogs.pick_open_file",
            lambda *a, on_done, **k: on_done(str(source)),
        )
        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *a, **k: QMessageBox.StandardButton.Yes,
        )
        information: list[tuple[str, str]] = []
        monkeypatch.setattr(
            QMessageBox,
            "information",
            lambda _parent, title, body, *a, **k: information.append((title, body)),
        )
        flashes: list[str] = []
        monkeypatch.setattr(tab, "_flash_save_status", flashes.append)
        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)

        tab._on_import_settings()

        assert len(received) == 1
        assert received[0].anki_deck_name == "Imported Deck"
        assert len(information) == 1
        assert "check_for_updates" in information[0][1]
        assert "Auto-update of yt-dlp was disabled (settings imported from an older version)." in information[0][1]
        assert flashes == []

    def test_clean_import_keeps_inline_imported_flash(self, tab, monkeypatch, tmp_path):
        import json

        from PyQt6.QtWidgets import QMessageBox

        source = tmp_path / "clean-settings.json"
        source.write_text(json.dumps({"anki_deck_name": "Clean Import"}), encoding="utf-8")
        monkeypatch.setattr(
            "anki_miner.gui.widgets.settings_tab.file_dialogs.pick_open_file",
            lambda *a, on_done, **k: on_done(str(source)),
        )
        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *a, **k: QMessageBox.StandardButton.Yes,
        )

        def fail_information(*_args, **_kwargs):
            raise AssertionError("clean import must not show an information dialog")

        monkeypatch.setattr(QMessageBox, "information", fail_information)
        flashes: list[str] = []
        monkeypatch.setattr(tab, "_flash_save_status", flashes.append)

        tab._on_import_settings()

        assert flashes == ["✓ Imported"]


class TestIPlusOneFilterRoundTrip:
    """Load/save round-trip for the i+1 sentence filter checkbox."""

    def test_loads_use_i_plus_one_filter_from_config(self, test_config: AnkiMinerConfig, qtbot):
        cfg_on = replace(test_config, use_i_plus_one_filter=True)
        widget = SettingsTab(cfg_on)
        qtbot.addWidget(widget)
        try:
            assert widget.filtering_panel.use_i_plus_one_checkbox.isChecked() is True
        finally:
            widget.deleteLater()

        cfg_off = replace(test_config, use_i_plus_one_filter=False)
        widget = SettingsTab(cfg_off)
        qtbot.addWidget(widget)
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
        tab.commit_settings()

        assert len(received) == 1
        assert received[0].use_i_plus_one_filter is True

        tab.filtering_panel.use_i_plus_one_checkbox.setChecked(False)
        tab.commit_settings()

        assert len(received) == 2
        assert received[1].use_i_plus_one_filter is False


class TestAnkiTagsRoundTrip:
    """Load/save round-trip for the anki_tags QLineEdit on the Anki settings panel."""

    def test_loads_anki_tags_from_config(self, test_config: AnkiMinerConfig, qtbot):
        cfg = replace(test_config, anki_tags="custom tag")
        widget = SettingsTab(cfg)
        qtbot.addWidget(widget)
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
        tab.commit_settings()

        assert len(received) == 1
        assert received[0].anki_tags == "new-tag another"


class TestExpressionAudioRoundTrip:
    """Load/save round-trip for the expression audio field (Issue #73).

    The dedicated enable checkbox was removed; the field name is the on/off
    switch (like Frequency/Pitch).
    """

    def test_field_defaults_blank(self, tab):
        # test_config does not map expression_audio (default "" → feature off).
        assert tab.anki_panel.expression_audio_field_input.text() == ""

    def test_loads_expression_audio_from_config(self, test_config: AnkiMinerConfig, qtbot):
        cfg = replace(
            test_config,
            anki_fields={**test_config.anki_fields, "expression_audio": "ExpressionAudio"},
        )
        widget = SettingsTab(cfg)
        qtbot.addWidget(widget)
        try:
            assert widget.anki_panel.expression_audio_field_input.text() == "ExpressionAudio"
        finally:
            widget.deleteLater()

    def test_saves_expression_audio_to_config(self, tab, monkeypatch, qtbot):
        from PyQt6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)

        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)

        tab.anki_panel.expression_audio_field_input.setText("ExpressionAudio")
        tab.commit_settings()

        assert len(received) == 1
        assert received[0].anki_fields["expression_audio"] == "ExpressionAudio"

        # Saved config reloads into a fresh tab with values preserved.
        widget = SettingsTab(received[0])
        qtbot.addWidget(widget)
        try:
            assert widget.anki_panel.expression_audio_field_input.text() == "ExpressionAudio"
        finally:
            widget.deleteLater()

    def test_saves_blank_expression_audio_field(self, tab, monkeypatch):
        from PyQt6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)

        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)

        tab.anki_panel.expression_audio_field_input.setText("")
        tab.commit_settings()

        assert len(received) == 1
        assert received[0].anki_fields["expression_audio"] == ""


class TestSentenceLengthFilterRoundTrip:
    """Load/save round-trip for the sentence-length filter widgets (Issue #33)."""

    def test_loads_sentence_length_filter_from_config(self, test_config: AnkiMinerConfig, qtbot):
        cfg = replace(
            test_config,
            use_sentence_length_filter=True,
            max_sentence_duration_seconds=7.5,
            max_sentence_chars=60,
        )
        widget = SettingsTab(cfg)
        qtbot.addWidget(widget)
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
        tab.commit_settings()

        assert len(received) == 1
        assert received[0].use_sentence_length_filter is True
        assert received[0].max_sentence_duration_seconds == pytest.approx(7.5)
        assert received[0].max_sentence_chars == 60


class TestDictsRootRoundTrip:
    """Load/save round-trip for the Issue #45 dictionary storage folder picker."""

    def test_loads_dicts_root_from_config(self, test_config: AnkiMinerConfig, tmp_path, qtbot):
        custom = tmp_path / "custom_dicts"
        custom.mkdir()
        cfg = replace(test_config, dicts_root=custom)
        widget = SettingsTab(cfg)
        qtbot.addWidget(widget)
        try:
            assert widget.dictionary_panel.get_dicts_root() == custom
        finally:
            widget.deleteLater()

    def test_save_propagates_new_dicts_root(self, test_config: AnkiMinerConfig, tmp_path, monkeypatch, qtbot):
        from PyQt6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)

        starting = tmp_path / "starting"
        starting.mkdir()
        cfg = replace(test_config, dicts_root=starting)
        widget = SettingsTab(cfg)
        qtbot.addWidget(widget)
        try:
            received: list[AnkiMinerConfig] = []
            widget.config_changed.connect(received.append)

            new_root = tmp_path / "new_root"
            new_root.mkdir()
            widget.dictionary_panel.dicts_root_selector.set_path(str(new_root))

            widget.commit_settings()

            assert len(received) == 1
            assert received[0].dicts_root == new_root
        finally:
            widget.deleteLater()

    def test_save_rejects_nonexistent_dicts_root(self, test_config: AnkiMinerConfig, tmp_path, monkeypatch, qtbot):
        """Picking a path that vanished between selection and commit keeps the
        last-good root (never writes the bad path) and shows the sticky inline
        warning — no modal, the rest of the commit still goes through."""
        from PyQt6.QtWidgets import QMessageBox

        starting = tmp_path / "starting"
        starting.mkdir()
        cfg = replace(test_config, dicts_root=starting)
        widget = SettingsTab(cfg)
        qtbot.addWidget(widget)
        try:

            def _no_modal(*args, **kwargs):  # pragma: no cover - failure path
                raise AssertionError("no modal may fire during an auto-save commit")

            monkeypatch.setattr(QMessageBox, "warning", _no_modal)
            monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)

            received: list[AnkiMinerConfig] = []
            widget.config_changed.connect(received.append)

            widget.dictionary_panel.dicts_root_selector.set_path(str(tmp_path / "does_not_exist"))
            widget.commit_settings()

            assert len(received) == 1, "the commit must still go through"
            assert received[0].dicts_root == starting, "invalid root must keep the last-good value"
            assert "⚠" in widget.save_status_label.text()
        finally:
            widget.deleteLater()

    def test_save_syncs_panel_dicts_root_to_new_root(self, test_config: AnkiMinerConfig, tmp_path, monkeypatch, qtbot):
        """After saving a changed Storage Folder, the dictionary panel's
        ``_dicts_root`` must follow so refresh_registry()/remove() target the new
        location — not the stale old one until restart (T-07)."""
        from PyQt6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)

        starting = tmp_path / "starting"
        starting.mkdir()
        cfg = replace(test_config, dicts_root=starting)
        widget = SettingsTab(cfg)
        qtbot.addWidget(widget)
        try:
            new_root = tmp_path / "new_root"
            new_root.mkdir()
            widget.dictionary_panel.dicts_root_selector.set_path(str(new_root))

            # Capture the root the panel rescans on the next registry refresh.
            import anki_miner.gui.widgets.panels.dictionary_settings_panel as dsp

            scanned_roots: list[Path] = []
            real_registry = dsp.DictionaryRegistry

            def _tracking_registry(root, *a, **kw):
                scanned_roots.append(root)
                return real_registry(root, *a, **kw)

            monkeypatch.setattr(dsp, "DictionaryRegistry", _tracking_registry)

            widget.commit_settings()

            # Panel state followed the saved root.
            assert widget.dictionary_panel._dicts_root == new_root
            assert widget.dictionary_panel.get_dicts_root() == new_root
            # A subsequent registry rescan targets the new root, not the old one.
            # The scan runs off the GUI thread, so wait for the worker to
            # construct the registry.
            widget.dictionary_panel.refresh_registry()
            qtbot.waitUntil(lambda: bool(scanned_roots), timeout=3000)
            qtbot.waitUntil(lambda: not widget.dictionary_panel._scan_in_flight, timeout=3000)
            assert scanned_roots[-1] == new_root
            assert starting not in scanned_roots
        finally:
            widget.deleteLater()

    def test_save_unchanged_dicts_root_does_not_reset_panel(
        self, test_config: AnkiMinerConfig, tmp_path, monkeypatch, qtbot
    ):
        """When the root is unchanged the panel must not be needlessly re-synced
        (only the changed-root path calls set_dicts_root) — T-07 scope guard."""
        from PyQt6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)

        starting = tmp_path / "starting"
        starting.mkdir()
        cfg = replace(test_config, dicts_root=starting)
        widget = SettingsTab(cfg)
        qtbot.addWidget(widget)
        try:
            calls: list[Path] = []
            real_set = widget.dictionary_panel.set_dicts_root

            def _spy(root):
                calls.append(root)
                return real_set(root)

            monkeypatch.setattr(widget.dictionary_panel, "set_dicts_root", _spy)

            # Selector still shows the current root → no change.
            widget.commit_settings()

            assert calls == [], "set_dicts_root must not run when the root is unchanged"
        finally:
            widget.deleteLater()

    def test_save_rejects_unwritable_dicts_root(self, test_config: AnkiMinerConfig, tmp_path, monkeypatch, qtbot):
        """A read-only directory must be rejected at Save so the user is not
        silently committed to a path the importers can't write to."""
        from PyQt6.QtWidgets import QMessageBox

        starting = tmp_path / "starting"
        starting.mkdir()
        readonly = tmp_path / "readonly"
        readonly.mkdir()
        cfg = replace(test_config, dicts_root=starting)
        widget = SettingsTab(cfg)
        qtbot.addWidget(widget)
        try:

            def _no_modal(*args, **kwargs):  # pragma: no cover - failure path
                raise AssertionError("no modal may fire during an auto-save commit")

            monkeypatch.setattr(QMessageBox, "warning", _no_modal)
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
            widget.commit_settings()

            assert len(received) == 1, "the commit must still go through"
            assert received[0].dicts_root == starting, "unwritable root must keep the last-good value"
            assert "⚠" in widget.save_status_label.text()
        finally:
            widget.deleteLater()


class TestDictionaryRemovedPersistsNarrowly:
    """chain_changed (from panel.remove()) must persist only the chain — never
    run the full Save pipeline whose unrelated validation aborts would orphan
    the removed dict_id in gui_config.json (Issue #30 / T-08 / OVH-032).

    The wiring is chain_changed → _persist_chain_change.  Since panel.remove()
    emits chain_changed (its sole persist trigger), we drive chain_changed
    directly here so the tests remain independent of disk state.
    """

    def test_removed_persists_chain_despite_failing_validation(self, test_config, tmp_path, monkeypatch, qtbot):
        """A stale (deleted) cookies file would abort the full Save at its
        validation gate — but the chain change after a destructive remove must
        still be persisted via chain_changed, with no warning dialog."""
        from PyQt6.QtWidgets import QMessageBox

        # A cookies path that does not exist → _on_save_clicked would early-return
        # at the cookies validation, orphaning the removed dict_id.
        cfg = replace(
            test_config,
            youtube_cookies_file=tmp_path / "gone.txt",
            dictionary_chain=(
                ChainEntry(kind="indexed", dict_id="dict-a", enabled=True),
                ChainEntry(kind="jisho", dict_id=None, enabled=True),
            ),
        )
        widget = SettingsTab(cfg)
        qtbot.addWidget(widget)
        try:
            warnings: list[tuple] = []
            monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a) or 0)
            monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

            received: list[AnkiMinerConfig] = []
            widget.config_changed.connect(received.append)

            # Simulate the panel state AFTER a remove: dict-a gone from the chain.
            widget.dictionary_panel.set_chain((ChainEntry(kind="jisho", dict_id=None, enabled=True),))

            # chain_changed is the signal that drives persist (OVH-032).
            widget.dictionary_panel.chain_changed.emit()

            assert received, "chain change must be persisted even though Save would have aborted"
            assert received[-1].dictionary_chain == (ChainEntry(kind="jisho", dict_id=None, enabled=True),)
            assert warnings == [], "the narrow persist must not pop a validation warning"
        finally:
            widget.deleteLater()

    def test_removed_does_not_commit_unrelated_pending_edit(self, test_config, monkeypatch, qtbot):
        """The success path of the full Save commits ALL panels' unsaved edits.
        The narrow persist must touch only dictionary_chain — a typed-but-unsaved
        deck name must not leak into the persisted config."""
        from PyQt6.QtWidgets import QMessageBox

        cfg = replace(
            test_config,
            anki_deck_name="original_deck",
            dictionary_chain=(
                ChainEntry(kind="indexed", dict_id="dict-a", enabled=True),
                ChainEntry(kind="jisho", dict_id=None, enabled=True),
            ),
        )
        widget = SettingsTab(cfg)
        qtbot.addWidget(widget)
        try:
            monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
            monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)

            received: list[AnkiMinerConfig] = []
            widget.config_changed.connect(received.append)

            # Unrelated pending edit the user has NOT saved.
            widget.anki_panel.set_deck_name("unsaved_deck")

            widget.dictionary_panel.set_chain((ChainEntry(kind="jisho", dict_id=None, enabled=True),))
            # chain_changed is the signal that drives persist (OVH-032).
            widget.dictionary_panel.chain_changed.emit()

            assert received, "chain change must be persisted"
            # Only the chain changed; the unrelated edit was not committed.
            assert received[-1].dictionary_chain == (ChainEntry(kind="jisho", dict_id=None, enabled=True),)
            assert received[-1].anki_deck_name == "original_deck"
        finally:
            widget.deleteLater()


class TestBlacklistWhitelistSelectorClearedOnNone:
    """_load_config must CLEAR the blacklist/whitelist selectors when the config
    path is None — otherwise a None-path reload leaves the old path visible and
    the next commit reads it back, re-persisting the stale path (T-11).

    Historically driven through Reset-to-Defaults; the button is gone, but the
    behavior (None-path load clears selectors + next commit persists None) is
    load-bearing under auto-save and stays covered via update_config."""

    def test_none_path_reload_clears_selectors_and_next_commit_persists_none(
        self, test_config, tmp_path, monkeypatch, qtbot
    ):
        from PyQt6.QtWidgets import QMessageBox

        bl = tmp_path / "blacklist.txt"
        bl.write_text("a\n", encoding="utf-8")
        wl = tmp_path / "whitelist.txt"
        wl.write_text("b\n", encoding="utf-8")
        cfg = replace(test_config, blacklist_path=bl, whitelist_path=wl)
        widget = SettingsTab(cfg)
        qtbot.addWidget(widget)
        try:
            # Loaded paths are visible.
            assert widget.filtering_panel.blacklist_selector.get_path() == str(bl)
            assert widget.filtering_panel.whitelist_selector.get_path() == str(wl)

            # External reload drops both paths (the same _load_config branch
            # the old Reset button exercised).
            monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
            widget.update_config(replace(cfg, blacklist_path=None, whitelist_path=None))

            # Selectors must be cleared, not left showing the stale paths.
            assert widget.filtering_panel.blacklist_selector.get_path() == ""
            assert widget.filtering_panel.whitelist_selector.get_path() == ""

            # The very next commit must persist None, not re-read the old path.
            received: list[AnkiMinerConfig] = []
            widget.config_changed.connect(received.append)
            widget.commit_settings()

            assert received, "commit should emit a config"
            assert received[-1].blacklist_path is None
            assert received[-1].whitelist_path is None
        finally:
            # commit_settings reconciles styling, spawning a short-lived AnkiConnect
            # worker; join it (mirroring the `tab` fixture) so a late signal cannot
            # fire into a torn-down widget and SIGABRT a later test on this worker.
            widget.shutdown()
            for w in widget.iter_close_workers():
                if w is not None:
                    w.wait(3000)
            qtbot.wait(10)
            with contextlib.suppress(RuntimeError):
                widget.deleteLater()

    def test_update_config_to_none_clears_previously_loaded_path(self, test_config, tmp_path, qtbot):
        """A programmatic update_config that drops the path must also clear the
        selector (the same _load_config branch Reset relies on)."""
        bl = tmp_path / "blacklist.txt"
        bl.write_text("a\n", encoding="utf-8")
        widget = SettingsTab(replace(test_config, blacklist_path=bl))
        qtbot.addWidget(widget)
        try:
            assert widget.filtering_panel.blacklist_selector.get_path() == str(bl)
            widget.update_config(replace(test_config, blacklist_path=None))
            assert widget.filtering_panel.blacklist_selector.get_path() == ""
        finally:
            widget.shutdown()
            for w in widget.iter_close_workers():
                if w is not None:
                    w.wait(3000)
            qtbot.wait(10)
            with contextlib.suppress(RuntimeError):
                widget.deleteLater()


class TestConfigChangePanelReload:
    """A config change reloads panels only when a panel-owned key changed (OVH-007)."""

    def test_non_panel_key_change_does_not_reload_panels(self, tab):
        """A change touching only a non-panel key must not reload panels (OVH-007)."""
        from unittest.mock import MagicMock

        tab._load_config = MagicMock()
        updated = replace(tab.config, skipped_update_version="9.9.9")

        tab.update_config(updated)

        tab._load_config.assert_not_called()


class TestSubtitlesPanelRegistration:
    """subtitles_panel is in _save_panels and reachable from the Settings navigator."""

    def test_subtitles_panel_in_save_panels(self, tab):
        assert tab.subtitles_panel in tab._save_panels

    def test_subtitles_destination_exists(self, tab):
        # By stable key, not by displayed name: the navigator label is
        # "Transcription & Alignment" and translates, the key never does.
        assert "subtitles" in tab._subtab_index
        tab.open_subtab("subtitles")
        assert tab.subtitles_panel in tab.pages.currentWidget().findChildren(type(tab.subtitles_panel))

    def test_subtitles_panel_loads_alass_location(self, test_config: AnkiMinerConfig, qtbot, tmp_path):
        alass_path = tmp_path / "alass"
        cfg = replace(test_config, alass_location=alass_path)
        widget = SettingsTab(cfg)
        qtbot.addWidget(widget)
        try:
            assert widget.subtitles_panel.alass_selector.get_path() == str(alass_path)
        finally:
            widget.deleteLater()

    def test_save_persists_alass_location(self, tab, monkeypatch, tmp_path):
        from PyQt6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

        alass_path = tmp_path / "alass"
        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)

        tab.subtitles_panel.alass_selector.set_path(str(alass_path))
        tab.commit_settings()

        assert len(received) == 1
        assert received[0].alass_location == alass_path

    def test_save_empty_alass_location_is_none(self, tab, monkeypatch):
        from PyQt6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)

        tab.subtitles_panel.alass_selector.set_path("")
        tab.commit_settings()

        assert len(received) == 1
        assert received[0].alass_location is None


def test_offline_load_and_save_preserves_deck_and_note_type(tab, test_config):
    """With Anki closed the combos have no fetched items; a save must not blank the config.

    This is the regression select_or_insert exists to prevent: a strict combo
    can only show values that are items, so a saved deck that was never
    inserted would read back as "" and the next auto-save would wipe it out of
    gui_config.json.
    """
    cfg = replace(test_config, anki_deck_name="JP::Mining", anki_note_type="Lapis")
    tab.config = cfg
    tab._load_config()
    saved = tab.anki_panel.contribute(cfg)
    assert saved.anki_deck_name == "JP::Mining"
    assert saved.anki_note_type == "Lapis"


def test_sync_buttons_refresh_the_name_lists(tab):
    from unittest.mock import patch  # noqa: PLC0415 — module convention

    with patch.object(tab._anki_probe, "refresh_name_lists") as refresh:
        tab.anki_panel.deck_sync_requested.emit()
        tab.anki_panel.notetype_sync_requested.emit()
    assert refresh.call_count == 2


def test_name_lists_are_fetched_once_on_first_show(tab):
    """Patching is mandatory: an unpatched show() opens a real AnkiConnect socket."""
    from unittest.mock import patch  # noqa: PLC0415 — module convention

    with patch.object(tab._anki_probe, "refresh_name_lists") as refresh:
        tab.show()
        tab.hide()
        tab.show()
    assert refresh.call_count == 1


def test_rebuild_known_words_does_not_block_gui_and_reenables_action(tab, tmp_path, monkeypatch, qtbot):
    import sqlite3
    import time

    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QMessageBox

    from anki_miner.services.known_word_db import KnownWordDB

    db_path = tmp_path / "known_words.db"
    db = KnownWordDB(db_path)
    db.initialize()
    db.add_words({"食べる"}, source="anki")
    tab.config = replace(tab.config, known_words_db_path=db_path)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)

    holder = sqlite3.connect(db_path)
    holder.execute("BEGIN IMMEDIATE")
    event_loop_tick: list[bool] = []
    QTimer.singleShot(0, lambda: event_loop_tick.append(True))
    workers = []
    try:
        started = time.monotonic()
        tab._on_rebuild_known_words()
        elapsed = time.monotonic() - started
        workers = list(getattr(tab, "_off_thread_workers", ()))

        assert elapsed < 0.5
        assert tab.filtering_panel.rebuild_known_words_button.isEnabled() is False
        qtbot.waitUntil(lambda: bool(event_loop_tick), timeout=500)
        assert len(workers) == 1
    finally:
        holder.rollback()
        holder.close()
        for worker in workers:
            worker.wait(6000)

    qtbot.waitUntil(lambda: tab.filtering_panel.rebuild_known_words_button.isEnabled(), timeout=1000)
