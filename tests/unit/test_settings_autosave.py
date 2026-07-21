"""Tests for the Settings tab's debounced auto-save (replaces the Save button).

Covers: edit-signal wiring (incl. nested FileSelector line edits), debounce
coalescing, the loading guard, per-field validation (invalid field keeps its
last-good value while the rest commits), pitch selector re-sync, and the
close-time flush that must never spin the modal zip import.
"""

from __future__ import annotations

import contextlib
from dataclasses import replace
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QMessageBox

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.settings_tab import SettingsTab


@pytest.fixture
def tab(test_config: AnkiMinerConfig, qtbot):
    """SettingsTab with a long debounce so tests control commit timing.

    Debounce-behavior tests shorten the interval themselves; everything else
    drives ``commit_settings()`` directly and must never see a timer fire.
    """
    widget = SettingsTab(test_config)
    qtbot.addWidget(widget)
    widget._debounce_timer.setInterval(60_000)
    yield widget
    widget.shutdown()
    for w in widget.iter_close_workers():
        if w is not None:
            w.wait(3000)
    qtbot.wait(10)
    with contextlib.suppress(RuntimeError):
        widget.deleteLater()


@pytest.fixture
def no_modals(monkeypatch):
    """Fail the test if any QMessageBox modal fires during a commit."""

    def _boom(*args, **kwargs):  # pragma: no cover - failure path
        raise AssertionError("modal QMessageBox during auto-save commit")

    monkeypatch.setattr(QMessageBox, "warning", _boom)
    monkeypatch.setattr(QMessageBox, "question", _boom)
    monkeypatch.setattr(QMessageBox, "information", _boom)


class TestDebounceWiring:
    def test_construction_leaves_debounce_idle(self, tab):
        assert not tab._debounce_timer.isActive()

    def test_line_edit_arms_debounce(self, tab):
        tab.deck_input.setText("NewDeck")
        assert tab._debounce_timer.isActive()

    def test_checkbox_arms_debounce(self, tab):
        box = tab.check_for_updates_checkbox
        box.setChecked(not box.isChecked())
        assert tab._debounce_timer.isActive()

    def test_nested_file_selector_arms_debounce(self, tab, tmp_path):
        # Filtering panel's blacklist FileSelector only exposes edits through
        # its nested QLineEdit — recursion in the wiring is load-bearing.
        tab.filtering_panel.blacklist_selector.set_path(str(tmp_path / "b.txt"))
        assert tab._debounce_timer.isActive()

    def test_dicts_root_selector_arms_debounce(self, tab, tmp_path):
        tab.dictionary_panel.dicts_root_selector.set_path(str(tmp_path))
        assert tab._debounce_timer.isActive()

    def test_reload_from_update_config_does_not_arm(self, tab, test_config):
        tab.update_config(replace(test_config, anki_deck_name="External"))
        assert not tab._debounce_timer.isActive()

    def test_debounced_edit_commits(self, tab, qtbot):
        tab._debounce_timer.setInterval(0)
        with qtbot.waitSignal(tab.config_changed, timeout=3000) as blocker:
            tab.deck_input.setText("Debounced")
        assert blocker.args[0].anki_deck_name == "Debounced"

    def test_burst_coalesces_to_single_commit(self, tab, qtbot):
        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)
        tab._debounce_timer.setInterval(50)
        for value in ("A", "AB", "ABC"):
            tab.deck_input.setText(value)
        qtbot.waitUntil(lambda: bool(received), timeout=3000)
        qtbot.wait(200)
        assert len(received) == 1
        assert received[0].anki_deck_name == "ABC"


class TestPerFieldValidation:
    def test_invalid_regex_keeps_last_good_and_commits_rest(self, tab, test_config, no_modals):
        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)
        tab.filtering_panel.use_subtitle_regex_checkbox.setChecked(True)
        tab.filtering_panel.subtitle_regex_edit.setText("[")
        tab.deck_input.setText("StillSaves")

        tab.commit_settings()

        assert len(received) == 1
        committed = received[0]
        assert committed.anki_deck_name == "StillSaves"
        assert committed.subtitle_regex_filter == test_config.subtitle_regex_filter
        assert committed.use_subtitle_regex_filter == test_config.use_subtitle_regex_filter
        assert "⚠" in tab.save_status_label.text()

    @pytest.mark.parametrize(
        ("pattern", "replacement"),
        [
            ("(", ""),
            (r"(a+)+$", ""),
            ("a" * 513, ""),
            ("a", "x" * 513),
            (r"(a)", r"\2"),
        ],
        ids=("invalid", "catastrophic", "long-pattern", "long-replacement", "bad-backreference"),
    )
    def test_invalid_or_catastrophic_regex_filter_rejected_at_commit(
        self, tab, test_config, no_modals, pattern, replacement
    ):
        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)
        tab.filtering_panel.set_subtitle_regex_filter(pattern)
        tab.filtering_panel.set_subtitle_regex_replacement(replacement)
        tab.filtering_panel.set_use_subtitle_regex_filter(True)

        tab.commit_settings()

        assert len(received) == 1
        committed = received[0]
        assert committed.subtitle_regex_filter == test_config.subtitle_regex_filter
        assert committed.subtitle_regex_replacement == test_config.subtitle_regex_replacement
        assert committed.use_subtitle_regex_filter == test_config.use_subtitle_regex_filter
        assert "⚠" in tab.save_status_label.text()

    def test_warning_is_sticky_until_next_valid_commit(self, tab, no_modals, qtbot):
        tab.filtering_panel.use_subtitle_regex_checkbox.setChecked(True)
        tab.filtering_panel.subtitle_regex_edit.setText("[")
        tab.commit_settings()
        assert "⚠" in tab.save_status_label.text()
        assert not tab._save_status_timer.isActive()

        tab.filtering_panel.subtitle_regex_edit.setText(r"\d+")
        tab.commit_settings()
        assert "✓" in tab.save_status_label.text()

    def test_valid_commit_flashes_saved(self, tab, no_modals):
        tab.deck_input.setText("FlashDeck")
        tab.commit_settings()
        assert "✓" in tab.save_status_label.text()

    def test_invalid_dicts_root_keeps_last_good_and_commits_rest(self, tab, test_config, no_modals):
        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)
        tab.dictionary_panel.dicts_root_selector.set_path("/nonexistent/nowhere")
        tab.deck_input.setText("RootDeck")

        tab.commit_settings()

        assert len(received) == 1
        assert received[0].anki_deck_name == "RootDeck"
        assert received[0].dicts_root == test_config.dicts_root
        assert "⚠" in tab.save_status_label.text()

    def test_missing_cookies_file_keeps_last_good_and_commits_rest(self, tab, test_config, no_modals):
        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)
        tab.youtube_panel.set_cookies_file(Path("/nonexistent/cookies.txt"))
        tab.deck_input.setText("CookieDeck")

        tab.commit_settings()

        assert len(received) == 1
        assert received[0].anki_deck_name == "CookieDeck"
        assert received[0].youtube_cookies_file == test_config.youtube_cookies_file
        assert "⚠" in tab.save_status_label.text()

    def test_valid_dicts_root_change_commits_and_syncs_panel(self, tab, tmp_path, no_modals):
        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)
        new_root = tmp_path / "new_dicts"
        new_root.mkdir()
        tab.dictionary_panel.dicts_root_selector.set_path(str(new_root))

        tab.commit_settings()

        assert received[-1].dicts_root == new_root


class TestPitchSelectorResync:
    def test_cancelled_zip_import_resyncs_selector_and_commits_rest(self, tab, test_config, no_modals, monkeypatch):
        monkeypatch.setattr(tab._zip_import_flow, "run_modal_zip_import", lambda **kw: None)
        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)
        tab.dictionary_panel.pitch_accent_selector.set_path("/tmp/pitch.zip")
        tab.deck_input.setText("PitchDeck")

        tab.commit_settings()

        assert len(received) == 1
        assert received[0].anki_deck_name == "PitchDeck"
        assert received[0].pitch_accent_path == test_config.pitch_accent_path
        selector_path = tab.dictionary_panel.pitch_accent_selector.get_path()
        assert selector_path == str(test_config.pitch_accent_path)

    def test_declined_overwrite_resyncs_selector(self, tab, test_config, no_modals, monkeypatch):
        # Overwrite-decline returns the fallback path (NOT None); the selector
        # must still be re-synced or the next commit re-pops the modal.
        monkeypatch.setattr(
            tab._zip_import_flow,
            "run_modal_zip_import",
            lambda **kw: test_config.pitch_accent_path,
        )
        tab.dictionary_panel.pitch_accent_selector.set_path("/tmp/pitch.zip")

        tab.commit_settings()

        selector_path = tab.dictionary_panel.pitch_accent_selector.get_path()
        assert selector_path == str(test_config.pitch_accent_path)

    def test_selector_resync_does_not_arm_debounce(self, tab, test_config, no_modals, monkeypatch):
        monkeypatch.setattr(tab._zip_import_flow, "run_modal_zip_import", lambda **kw: None)
        tab.dictionary_panel.pitch_accent_selector.set_path("/tmp/pitch.zip")
        tab.commit_settings()
        assert not tab._debounce_timer.isActive()


class TestFlushAndShutdown:
    def test_flush_commits_pending_edit_exactly_once(self, tab, no_modals):
        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)
        tab.deck_input.setText("Flushed")
        assert tab._debounce_timer.isActive()

        tab.flush_pending_settings()

        assert len(received) == 1
        assert received[0].anki_deck_name == "Flushed"
        assert not tab._debounce_timer.isActive()

        tab.flush_pending_settings()
        assert len(received) == 1

    def test_flush_skips_modal_zip_import(self, tab, test_config, no_modals, monkeypatch):
        def _boom(**kwargs):  # pragma: no cover - failure path
            raise AssertionError("modal zip import ran during close flush")

        monkeypatch.setattr(tab._zip_import_flow, "run_modal_zip_import", _boom)
        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)
        tab.dictionary_panel.pitch_accent_selector.set_path("/tmp/pending.zip")
        tab.deck_input.setText("CloseDeck")

        tab.flush_pending_settings()

        assert len(received) == 1
        assert received[0].anki_deck_name == "CloseDeck"
        assert received[0].pitch_accent_path == test_config.pitch_accent_path

    def test_shutdown_stops_armed_timer(self, tab):
        tab.deck_input.setText("Pending")
        assert tab._debounce_timer.isActive()
        tab.shutdown()
        assert not tab._debounce_timer.isActive()


class TestCommitRetainsSaveSemantics:
    def test_reenabling_update_checks_clears_skipped_version(self, tab, test_config, no_modals):
        tab.update_config(replace(test_config, check_for_updates=False, skipped_update_version="9.9.9"))
        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)
        tab.check_for_updates_checkbox.setChecked(True)
        tab.commit_settings()
        assert received[-1].check_for_updates is True
        assert received[-1].skipped_update_version == ""


class TestManualControlsRemoved:
    """Auto-save replaces the Save button; the destructive Reset button dies
    with it. Neither widget nor their Ctrl+S/Ctrl+R shortcuts may remain."""

    def test_save_and_reset_buttons_gone(self, tab):
        assert not hasattr(tab, "save_button")
        assert not hasattr(tab, "reset_button")

    def test_ctrl_s_and_ctrl_r_shortcuts_gone(self, tab):
        from PyQt6.QtGui import QShortcut

        sequences = {s.key().toString() for s in tab.findChildren(QShortcut)}
        assert "Ctrl+S" not in sequences
        assert "Ctrl+R" not in sequences
