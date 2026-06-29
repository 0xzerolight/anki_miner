"""Tests for the settings tab, focused on the YouTube settings panel wiring."""

from __future__ import annotations

import contextlib
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

    def test_save_flashes_inline_status_not_popup(self, tab, monkeypatch):
        """A successful save shows the inline label, not a modal popup."""
        from PyQt6.QtWidgets import QMessageBox

        def _fail(*_a, **_k):
            raise AssertionError("save must not show a modal popup")

        monkeypatch.setattr(QMessageBox, "information", _fail)

        assert tab.save_status_label.text() == ""
        tab._on_save_clicked()

        assert "Saved" in tab.save_status_label.text()
        assert tab._save_status_timer.isActive()

    def test_reset_flashes_inline_status(self, tab, monkeypatch):
        """A confirmed reset shows the inline label, not the old info popup."""
        from PyQt6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
        monkeypatch.setattr(
            QMessageBox,
            "information",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("reset must not show a modal popup")),
        )

        tab._on_reset_clicked()

        assert "Reset to defaults" in tab.save_status_label.text()

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
        tab._on_save_clicked()

        assert len(received) == 1
        assert received[0].use_i_plus_one_filter is True

        tab.filtering_panel.use_i_plus_one_checkbox.setChecked(False)
        tab._on_save_clicked()

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
        tab._on_save_clicked()

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
        tab._on_save_clicked()

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
        tab._on_save_clicked()

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
        tab._on_save_clicked()

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

            widget._on_save_clicked()

            assert len(received) == 1
            assert received[0].dicts_root == new_root
        finally:
            widget.deleteLater()

    def test_save_rejects_nonexistent_dicts_root(self, test_config: AnkiMinerConfig, tmp_path, monkeypatch, qtbot):
        """Picking a path that vanished between selection and save must surface a
        warning and abort — never write Path('') to the config."""
        from PyQt6.QtWidgets import QMessageBox

        starting = tmp_path / "starting"
        starting.mkdir()
        cfg = replace(test_config, dicts_root=starting)
        widget = SettingsTab(cfg)
        qtbot.addWidget(widget)
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

            widget._on_save_clicked()

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
            widget._on_save_clicked()

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
    """chain_changed (from panel.remove()) must persist only the chain — never
    run the full Save pipeline whose unrelated validation aborts would orphan
    the removed dict_id in gui_config.json (Issue #30 / T-08 / OVH-032).

    The wiring is chain_changed → _persist_chain_change.  Since panel.remove()
    emits chain_changed then dictionary_removed, we drive chain_changed directly
    here so the tests remain independent of disk state.
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
            widget.anki_panel.deck_input.setText("unsaved_deck")

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
    path is None — otherwise Reset-to-Defaults leaves the old path visible and
    the next Save reads it back, re-persisting the stale path (T-11)."""

    def test_reset_clears_selectors_and_next_save_persists_none(self, test_config, tmp_path, monkeypatch, qtbot):
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
            widget.deleteLater()


class TestCardStylingSyncWiring:
    """Save triggers a styling sync; the connect signal triggers a reconcile (Issue #44)."""

    def test_save_calls_sync_styling(self, tab, monkeypatch):
        from unittest.mock import MagicMock

        from PyQt6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)
        tab._anki_probe.sync_styling = MagicMock()

        tab._on_save_clicked()

        tab._anki_probe.sync_styling.assert_called_once()

    def test_notify_anki_connected_syncs(self, tab):
        from unittest.mock import MagicMock

        tab._anki_probe.sync_styling = MagicMock()

        tab.notify_anki_connected()

        tab._anki_probe.sync_styling.assert_called_once()

    def test_chain_change_resyncs_styling_when_managing(self, tab):
        """A dictionary-chain change re-pushes the managed glossary CSS."""
        from unittest.mock import MagicMock

        tab.config = replace(tab.config, manage_card_styling=True)
        tab._anki_probe.sync_styling = MagicMock()

        tab._persist_chain_change(tab.config.dictionary_chain)

        tab._anki_probe.sync_styling.assert_called_once()

    def test_non_panel_key_change_does_not_reload_panels(self, tab):
        """A change touching only a non-panel key must not reload panels (OVH-007)."""
        from unittest.mock import MagicMock

        tab._load_config = MagicMock()
        updated = replace(tab.config, skipped_update_version="9.9.9")

        tab.update_config(updated)

        tab._load_config.assert_not_called()


class TestSubtitlesPanelRegistration:
    """subtitles_panel is in _save_panels and its tab appears in the Settings tab widget."""

    def test_subtitles_panel_in_save_panels(self, tab):
        assert tab.subtitles_panel in tab._save_panels

    def test_subtitles_tab_exists(self, tab):
        tab_titles = [tab.tab_widget.tabText(i) for i in range(tab.tab_widget.count())]
        assert "Subtitles" in tab_titles

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
        tab._on_save_clicked()

        assert len(received) == 1
        assert received[0].alass_location == alass_path

    def test_save_empty_alass_location_is_none(self, tab, monkeypatch):
        from PyQt6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)

        tab.subtitles_panel.alass_selector.set_path("")
        tab._on_save_clicked()

        assert len(received) == 1
        assert received[0].alass_location is None
