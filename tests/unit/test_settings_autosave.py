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

from anki_miner.config import AnkiMinerConfig, ChainEntry
from anki_miner.gui.utils.config_commit import ConfigCommitError, ConfigCommitResult
from anki_miner.gui.widgets.settings_tab import SettingsTab
from anki_miner.gui.workers.import_worker import ImportWorker
from anki_miner.services.dictionary.registry import DictionaryRegistry


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
        # Deliberately a QLineEdit: the deck row is a QComboBox now, so this
        # test would no longer cover the QLineEdit branch of _wire_edit_signals.
        tab.anki_panel.anki_tags_input.setText("new-tag")
        assert tab._debounce_timer.isActive()

    def test_combo_arms_debounce(self, tab):
        tab.anki_panel.set_deck_name("NewDeck")
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
            tab.anki_panel.set_deck_name("Debounced")
        assert blocker.args[0].anki_deck_name == "Debounced"

    def test_burst_coalesces_to_single_commit(self, tab, qtbot):
        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)
        tab._debounce_timer.setInterval(50)
        for value in ("A", "AB", "ABC"):
            tab.anki_panel.set_deck_name(value)
        qtbot.waitUntil(lambda: bool(received), timeout=3000)
        qtbot.wait(200)
        assert len(received) == 1
        assert received[0].anki_deck_name == "ABC"

    def test_debounce_retries_while_panel_mutation_token_is_active(self, tab, qtbot):
        received: list[AnkiMinerConfig] = []
        timeout_count: list[None] = []
        tab.config_changed.connect(received.append)
        tab._debounce_timer.timeout.connect(lambda: timeout_count.append(None))
        tab._debounce_timer.setInterval(20)
        token = tab.dictionary_panel.hold_mutation("scan")
        tab.anki_panel.set_deck_name("WaitForToken")

        qtbot.waitUntil(lambda: bool(timeout_count), timeout=3000)

        assert received == []
        assert tab._debounce_timer.isActive()

        with qtbot.waitSignal(tab.config_changed, timeout=3000) as blocker:
            tab.dictionary_panel.release(token)
        assert blocker.args[0].anki_deck_name == "WaitForToken"


class TestCommitSelfEcho:
    def test_debounced_commit_adopts_echo_without_reloading_panels(self, tab):
        tab.config_changed.connect(tab.update_config)
        tab.anki_panel.anki_tags_input.setText("saved-tag")
        load_calls: list[None] = []
        original = tab._load_config

        def spy_load():
            load_calls.append(None)
            original()

        tab._load_config = spy_load

        tab.commit_settings()

        assert load_calls == []
        assert tab.config.anki_tags == "saved-tag"

    def test_dicts_root_commit_self_echo_syncs_panel_registry(self, tab, tmp_path, no_modals, monkeypatch):
        scan_roots: list[Path] = []
        monkeypatch.setattr(DictionaryRegistry, "load", lambda registry: scan_roots.append(registry._root))
        tab.config_changed.connect(tab.update_config)
        new_root = tmp_path / "new_dicts"
        new_root.mkdir()
        tab.dictionary_panel.dicts_root_selector.set_path(str(new_root))

        tab.commit_settings()

        assert tab.config.dicts_root == new_root
        assert tab.dictionary_panel._dicts_root == new_root
        tab.dictionary_panel._build_view()
        assert scan_roots == [new_root]

    @pytest.mark.parametrize("persisted", [False, True], ids=["pre-save", "post-save"])
    def test_dicts_root_commit_failure_keeps_all_consumers_on_durable_root(
        self, tab, tmp_path, no_modals, monkeypatch, qtbot, persisted
    ):
        scan_roots: list[Path] = []
        import_roots: list[Path] = []
        monkeypatch.setattr(DictionaryRegistry, "load", lambda registry: scan_roots.append(registry._root))
        old_root = tab.config.dicts_root

        def commit_then_fail(config):
            if persisted:
                tab.update_config(replace(config, config_version=config.config_version + 1))
                result = ConfigCommitResult.post_save_failure(RuntimeError("refresh failed"))
            else:
                result = ConfigCommitResult.pre_save_failure(OSError("disk full"))
            raise ConfigCommitError(result)

        tab._commit_config = commit_then_fail
        new_root = tmp_path / "new_dicts"
        new_root.mkdir()
        tab.dictionary_panel.dicts_root_selector.set_path(str(new_root))

        assert tab.commit_pending_settings_for_mutation() is False
        expected_root = new_root if persisted else old_root
        assert tab.config.dicts_root == expected_root
        assert tab.dictionary_panel.get_dicts_root() == new_root
        assert tab.dictionary_panel._dicts_root == expected_root

        tab.dictionary_panel._build_view()
        tab.dictionary_panel.refresh_registry()
        qtbot.waitUntil(lambda: len(scan_roots) == 2, timeout=3000)
        assert scan_roots == [expected_root, expected_root]

        def probe_import(_zip_path: Path, dest_root: Path):
            import_roots.append(dest_root)
            raise RuntimeError("import probe")

        monkeypatch.setattr(ImportWorker, "for_yomitan", staticmethod(probe_import))
        # Add is a batch now: a worker that cannot even be constructed is that
        # item's failure, reported as a banner, not an exception out of the slot.
        issues: list[tuple[str, str]] = []
        monkeypatch.setattr(
            tab._dict_import_flow,
            "_report_import_issue",
            lambda summary, details="": issues.append((summary, details)),
        )
        tab._dict_import_flow._add_dict_picked("trace", 0.0, [str(tmp_path / "dict.zip")])
        # The batch runner defers each job through QTimer.singleShot(0, …).
        qtbot.waitUntil(lambda: bool(issues), timeout=3000)
        assert import_roots == [expected_root]
        assert "import probe" in issues[0][1]

        entry = ChainEntry(kind="indexed", dict_id="test-dict")
        assert tab.dictionary_panel._entry_disk_dir(entry) == expected_root / "test-dict"

    def test_zoom_commit_preserves_pending_panel_edit(self, tab):
        tab.config_changed.connect(tab.update_config)
        tab.anki_panel.anki_tags_input.setText("pending-tag")

        tab._on_zoom_changed(1.25)

        assert tab.anki_panel.get_anki_tags() == "pending-tag"
        assert tab.config.ui_zoom == 1.25
        assert tab.config.anki_tags != "pending-tag"
        assert tab._settings_dirty is True

    def test_native_dialog_commit_preserves_pending_panel_edit(self, tab):
        tab.config_changed.connect(tab.update_config)
        tab.anki_panel.anki_tags_input.setText("pending-tag")
        use_native = not tab.config.use_native_file_dialogs

        tab._on_native_dialogs_changed(use_native)

        assert tab.anki_panel.get_anki_tags() == "pending-tag"
        assert tab.config.use_native_file_dialogs is use_native
        assert tab.config.anki_tags != "pending-tag"
        assert tab._settings_dirty is True


class TestPerFieldValidation:
    def test_invalid_regex_keeps_last_good_and_commits_rest(self, tab, test_config, no_modals):
        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)
        tab.filtering_panel.use_subtitle_regex_checkbox.setChecked(True)
        tab.filtering_panel.subtitle_regex_edit.setText("[")
        tab.anki_panel.set_deck_name("StillSaves")

        tab.commit_settings()

        assert len(received) == 1
        committed = received[0]
        assert committed.anki_deck_name == "StillSaves"
        assert committed.subtitle_regex_filter == test_config.subtitle_regex_filter
        assert committed.use_subtitle_regex_filter == test_config.use_subtitle_regex_filter
        assert "⚠" in tab.save_status_label.text()

    def test_invalid_regex_is_rejected_when_filter_is_disabled(self, tab, test_config, no_modals):
        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)
        tab.filtering_panel.set_subtitle_regex_filter("(")
        tab.filtering_panel.set_subtitle_regex_replacement("NEW")
        tab.filtering_panel.set_use_subtitle_regex_filter(False)

        tab.commit_settings()

        assert len(received) == 1
        committed = received[0]
        assert committed.subtitle_regex_filter == test_config.subtitle_regex_filter
        assert committed.subtitle_regex_replacement == test_config.subtitle_regex_replacement
        assert committed.use_subtitle_regex_filter == test_config.use_subtitle_regex_filter
        assert "⚠" in tab.save_status_label.text()

    @pytest.mark.parametrize(
        ("pattern", "replacement"),
        [
            ("(", ""),
            (r"(a+)+$", ""),
            (r"^(a|aa)+$", ""),
            ("a" * 513, ""),
            ("a", "x" * 513),
            (r"(a)", r"\2"),
        ],
        ids=(
            "invalid",
            "catastrophic",
            "overlapping-alternation",
            "long-pattern",
            "long-replacement",
            "bad-backreference",
        ),
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
        tab.anki_panel.set_deck_name("FlashDeck")
        tab.commit_settings()
        assert "✓" in tab.save_status_label.text()

    def test_invalid_dicts_root_keeps_last_good_and_commits_rest(self, tab, test_config, no_modals):
        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)
        tab.dictionary_panel.dicts_root_selector.set_path("/nonexistent/nowhere")
        tab.anki_panel.set_deck_name("RootDeck")

        tab.commit_settings()

        assert len(received) == 1
        assert received[0].anki_deck_name == "RootDeck"
        assert received[0].dicts_root == test_config.dicts_root
        assert "⚠" in tab.save_status_label.text()

    def test_missing_cookies_file_keeps_last_good_and_commits_rest(self, tab, test_config, no_modals):
        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)
        tab.youtube_panel.set_cookies_file(Path("/nonexistent/cookies.txt"))
        tab.anki_panel.set_deck_name("CookieDeck")

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


class TestFlushAndShutdown:
    def test_flush_commits_pending_edit_exactly_once(self, tab, no_modals):
        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)
        tab.anki_panel.set_deck_name("Flushed")
        assert tab._debounce_timer.isActive()

        tab.flush_pending_settings()

        assert len(received) == 1
        assert received[0].anki_deck_name == "Flushed"
        assert not tab._debounce_timer.isActive()

        tab.flush_pending_settings()
        assert len(received) == 1

    def test_shutdown_stops_armed_timer(self, tab):
        tab.anki_panel.set_deck_name("Pending")
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
