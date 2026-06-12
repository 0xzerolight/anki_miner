"""Tests for the settings tab, focused on the YouTube settings panel wiring."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication

from anki_miner.config import AnkiMinerConfig, ChainEntry
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

    def test_playlist_max_defaults_to_100(self, tab):
        panel = tab.youtube_panel
        # test_config does not override youtube_playlist_max, so default is 100.
        assert panel.playlist_max_spinbox.value() == 100
        assert panel.get_playlist_max() == 100


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
            panel.set_cookies_from_browser("netscape")  # type: ignore[arg-type]
            assert panel.get_cookies_from_browser() is None
        finally:
            panel.deleteLater()

    def test_set_and_get_cookies_file_round_trip(self, tmp_path):
        panel = YouTubeSettingsPanel()
        try:
            cookies = tmp_path / "cookies.txt"
            panel.set_cookies_file(cookies)
            assert panel.get_cookies_file() == str(cookies)
        finally:
            panel.deleteLater()

    def test_cookies_file_defaults_to_empty(self):
        panel = YouTubeSettingsPanel()
        try:
            assert panel.get_cookies_file() == ""
        finally:
            panel.deleteLater()

    def test_set_cookies_file_none_clears_field(self, tmp_path):
        panel = YouTubeSettingsPanel()
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
    def test_set_and_get_max_duration(self, seconds, expected_minutes):
        panel = YouTubeSettingsPanel()
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
    def test_set_and_get_playlist_max(self, value, expected):
        panel = YouTubeSettingsPanel()
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

        tab._on_save_clicked()

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
        tab._on_save_clicked()

        assert len(received) == 1
        assert received[0].youtube_playlist_max == 250

    def test_load_config_reflects_playlist_max(self, test_config):
        cfg = replace(test_config, youtube_playlist_max=42)
        widget = SettingsTab(cfg)
        try:
            assert widget.youtube_panel.get_playlist_max() == 42
            assert widget.youtube_panel.playlist_max_spinbox.value() == 42
        finally:
            widget.deleteLater()

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

    def test_load_config_reflects_cookies_file(self, test_config, tmp_path):
        cookies = tmp_path / "cookies.txt"
        cfg = replace(test_config, youtube_cookies_file=cookies)
        widget = SettingsTab(cfg)
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

        tab._on_save_clicked()

        assert len(received) == 1
        assert received[0].youtube_cookies_file == cookies

    def test_save_empty_cookies_file_is_none(self, tab, monkeypatch):
        from PyQt6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)
        tab.youtube_panel.set_cookies_file("")

        tab._on_save_clicked()

        assert len(received) == 1
        assert received[0].youtube_cookies_file is None

    def test_save_blocks_on_missing_cookies_file(self, tab, monkeypatch, tmp_path):
        from PyQt6.QtWidgets import QMessageBox

        warnings: list[tuple] = []
        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
        monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a))

        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)
        # A path that does not exist must block the save.
        tab.youtube_panel.set_cookies_file(tmp_path / "does-not-exist.txt")

        tab._on_save_clicked()

        assert warnings, "expected a warning dialog for the missing cookies file"
        assert received == [], "config must not be emitted when validation fails"


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


class TestExpressionAudioRoundTrip:
    """Load/save round-trip for the expression audio toggle + field (Issue #73)."""

    def test_toggle_defaults_to_config_default(self, tab):
        # test_config does not override expression_audio_enabled (default False).
        assert tab.anki_panel.get_expression_audio_enabled() is False

    def test_loads_expression_audio_from_config(self, test_config: AnkiMinerConfig):
        cfg = replace(
            test_config,
            expression_audio_enabled=True,
            anki_fields={**test_config.anki_fields, "expression_audio": "ExpressionAudio"},
        )
        widget = SettingsTab(cfg)
        try:
            assert widget.anki_panel.get_expression_audio_enabled() is True
            assert widget.anki_panel.expression_audio_field_input.text() == "ExpressionAudio"
        finally:
            widget.deleteLater()

    def test_saves_expression_audio_to_config(self, tab, monkeypatch):
        from PyQt6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)

        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)

        tab.anki_panel.set_expression_audio_enabled(True)
        tab.anki_panel.expression_audio_field_input.setText("ExpressionAudio")
        tab._on_save_clicked()

        assert len(received) == 1
        assert received[0].expression_audio_enabled is True
        assert received[0].anki_fields["expression_audio"] == "ExpressionAudio"

        # Saved config reloads into a fresh tab with values preserved.
        widget = SettingsTab(received[0])
        try:
            assert widget.anki_panel.get_expression_audio_enabled() is True
            assert widget.anki_panel.expression_audio_field_input.text() == "ExpressionAudio"
        finally:
            widget.deleteLater()

    def test_saves_blank_expression_audio_field(self, tab, monkeypatch):
        from PyQt6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)

        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)

        tab.anki_panel.expression_audio_field_input.setText("")
        tab._on_save_clicked()

        assert len(received) == 1
        assert received[0].expression_audio_enabled is False
        assert received[0].anki_fields["expression_audio"] == ""


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

    def test_save_syncs_panel_dicts_root_to_new_root(self, test_config: AnkiMinerConfig, tmp_path, monkeypatch):
        """After saving a changed Storage Folder, the dictionary panel's
        ``_dicts_root`` must follow so refresh_registry()/remove() target the new
        location — not the stale old one until restart (T-07)."""
        from PyQt6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)

        starting = tmp_path / "starting"
        starting.mkdir()
        cfg = replace(test_config, dicts_root=starting)
        widget = SettingsTab(cfg)
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

            widget._on_save_clicked()

            # Panel state followed the saved root.
            assert widget.dictionary_panel._dicts_root == new_root
            assert widget.dictionary_panel.get_dicts_root() == new_root
            # A subsequent registry rescan targets the new root, not the old one.
            widget.dictionary_panel.refresh_registry()
            assert scanned_roots, "registry should have been rescanned"
            assert scanned_roots[-1] == new_root
            assert starting not in scanned_roots
        finally:
            widget.deleteLater()

    def test_save_unchanged_dicts_root_does_not_reset_panel(self, test_config: AnkiMinerConfig, tmp_path, monkeypatch):
        """When the root is unchanged the panel must not be needlessly re-synced
        (only the changed-root path calls set_dicts_root) — T-07 scope guard."""
        from PyQt6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)

        starting = tmp_path / "starting"
        starting.mkdir()
        cfg = replace(test_config, dicts_root=starting)
        widget = SettingsTab(cfg)
        try:
            calls: list[Path] = []
            real_set = widget.dictionary_panel.set_dicts_root

            def _spy(root):
                calls.append(root)
                return real_set(root)

            monkeypatch.setattr(widget.dictionary_panel, "set_dicts_root", _spy)

            # Selector still shows the current root → no change.
            widget._on_save_clicked()

            assert calls == [], "set_dicts_root must not run when the root is unchanged"
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


class TestDictionaryRemovedPersistsNarrowly:
    """dictionary_removed must persist only the chain — never run the full Save
    pipeline whose unrelated validation aborts would orphan the removed dict_id
    in gui_config.json (Issue #30 / T-08)."""

    def test_removed_persists_chain_despite_failing_validation(self, test_config, tmp_path, monkeypatch):
        """A stale (deleted) cookies file would abort the full Save at its
        validation gate — but the chain change after a destructive remove must
        still be persisted, with no warning dialog."""
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
        try:
            warnings: list[tuple] = []
            monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a) or 0)
            monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

            received: list[AnkiMinerConfig] = []
            widget.config_changed.connect(received.append)

            # Simulate the panel state AFTER a remove: dict-a gone from the chain.
            widget.dictionary_panel.set_chain((ChainEntry(kind="jisho", dict_id=None, enabled=True),))

            widget.dictionary_panel.dictionary_removed.emit()

            assert received, "chain change must be persisted even though Save would have aborted"
            assert received[-1].dictionary_chain == (ChainEntry(kind="jisho", dict_id=None, enabled=True),)
            assert warnings == [], "the narrow persist must not pop a validation warning"
        finally:
            widget.deleteLater()

    def test_removed_does_not_commit_unrelated_pending_edit(self, test_config, monkeypatch):
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
        try:
            monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
            monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)

            received: list[AnkiMinerConfig] = []
            widget.config_changed.connect(received.append)

            # Unrelated pending edit the user has NOT saved.
            widget.anki_panel.deck_input.setText("unsaved_deck")

            widget.dictionary_panel.set_chain((ChainEntry(kind="jisho", dict_id=None, enabled=True),))
            widget.dictionary_panel.dictionary_removed.emit()

            assert received, "chain change must be persisted"
            # Only the chain changed; the unrelated edit was not committed.
            assert received[-1].dictionary_chain == (ChainEntry(kind="jisho", dict_id=None, enabled=True),)
            assert received[-1].anki_deck_name == "original_deck"
        finally:
            widget.deleteLater()


class TestBlacklistWhitelistSelectorClearedOnNone:
    """_load_config must CLEAR the blacklist/whitelist selectors when the config
    path is None — otherwise Reset-to-Defaults leaves the old path visible and
    the next Save reads it back, re-persisting the stale path (T-11)."""

    def test_reset_clears_selectors_and_next_save_persists_none(self, test_config, tmp_path, monkeypatch):
        from PyQt6.QtWidgets import QMessageBox

        bl = tmp_path / "blacklist.txt"
        bl.write_text("a\n", encoding="utf-8")
        wl = tmp_path / "whitelist.txt"
        wl.write_text("b\n", encoding="utf-8")
        cfg = replace(test_config, blacklist_path=bl, whitelist_path=wl)
        widget = SettingsTab(cfg)
        try:
            # Loaded paths are visible.
            assert widget.filtering_panel.blacklist_selector.get_path() == str(bl)
            assert widget.filtering_panel.whitelist_selector.get_path() == str(wl)

            # Reset to defaults (paths become None) — confirm Yes, suppress info.
            monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
            monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
            widget._on_reset_clicked()

            # Selectors must be cleared, not left showing the stale paths.
            assert widget.filtering_panel.blacklist_selector.get_path() == ""
            assert widget.filtering_panel.whitelist_selector.get_path() == ""

            # The very next Save must persist None, not re-read the old path.
            received: list[AnkiMinerConfig] = []
            widget.config_changed.connect(received.append)
            widget._on_save_clicked()

            assert received, "save should emit a config"
            assert received[-1].blacklist_path is None
            assert received[-1].whitelist_path is None
        finally:
            widget.deleteLater()

    def test_update_config_to_none_clears_previously_loaded_path(self, test_config, tmp_path):
        """A programmatic update_config that drops the path must also clear the
        selector (the same _load_config branch Reset relies on)."""
        bl = tmp_path / "blacklist.txt"
        bl.write_text("a\n", encoding="utf-8")
        widget = SettingsTab(replace(test_config, blacklist_path=bl))
        try:
            assert widget.filtering_panel.blacklist_selector.get_path() == str(bl)
            widget.update_config(replace(test_config, blacklist_path=None))
            assert widget.filtering_panel.blacklist_selector.get_path() == ""
        finally:
            widget.deleteLater()
